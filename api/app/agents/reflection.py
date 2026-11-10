"""Risk-based, tool-less LLM critic for evidence-grounded resolution drafts."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.agents.schemas import AgentExecutionStatus, AgentTokenUsage
from app.common.redaction import mask_pii, redact_sensitive_data
from app.config import settings
from app.prompts.reflection_critic import REFLECTION_CRITIC_SYSTEM_PROMPT
from app.providers import LLMMessage, LLMProvider, LLMRequest, get_llm_provider
from app.schemas.evidence import EvidenceReference, VerifiedFact


class ReflectionVerdict(StrEnum):
    approve = "approve"
    revise = "revise"
    investigate = "investigate"
    clarify = "clarify"
    escalate = "escalate"


class ReflectionNextStep(StrEnum):
    deliver = "deliver"
    revise = "revise"
    investigate = "investigate"
    clarify = "clarify"
    escalate = "escalate"


class ReflectionIssueType(StrEnum):
    unsupported_claim = "unsupported_claim"
    missing_answer = "missing_answer"
    conflicting_evidence = "conflicting_evidence"
    policy_interpretation = "policy_interpretation"
    uncertainty = "uncertainty"
    action_state = "action_state"
    misleading_conclusion = "misleading_conclusion"


class RequiredFact(StrEnum):
    order_id = "order_id"
    tracking_number = "tracking_number"
    refund_reference = "refund_reference"
    account_verification = "account_verification"
    requested_action = "requested_action"
    policy_scope = "policy_scope"


class ReflectionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_type: ReflectionIssueType
    description: str = Field(min_length=1, max_length=400)
    evidence_references: tuple[str, ...] = ()

    @field_validator("evidence_references")
    @classmethod
    def require_opaque_references(cls, references: tuple[str, ...]) -> tuple[str, ...]:
        if any(not reference.startswith("ref_") for reference in references):
            raise ValueError("reflection issues may only cite opaque ref_ references")
        return references


class ReflectionInput(BaseModel):
    """Minimum safe context supplied to the critic; never raw connector payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    customer_request: str = Field(min_length=1, max_length=2_000)
    draft_response: str = Field(min_length=1, max_length=3_000)
    findings: list[dict] = Field(default_factory=list, max_length=6)
    facts: tuple[VerifiedFact, ...] = ()
    citations: tuple[EvidenceReference, ...] = ()


class ReflectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: ReflectionVerdict
    grounded: bool
    complete: bool
    issues: tuple[ReflectionIssue, ...] = ()
    recommended_next_step: ReflectionNextStep
    missing_information: tuple[RequiredFact, ...] = ()
    token_usage: AgentTokenUsage = Field(default_factory=AgentTokenUsage)
    latency_ms: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def validate_verdict_contract(self) -> "ReflectionResult":
        expected_step = ReflectionNextStep(self.verdict.value if self.verdict != ReflectionVerdict.approve else "deliver")
        if self.recommended_next_step != expected_step:
            raise ValueError("recommended_next_step must match the controlled verdict")
        if self.verdict == ReflectionVerdict.approve and (not self.grounded or not self.complete):
            raise ValueError("approval requires grounded and complete findings")
        if self.verdict == ReflectionVerdict.approve and self.issues:
            raise ValueError("approval cannot include unresolved issues")
        return self


class ReflectionAgentError(RuntimeError):
    """Invalid, failed, or timed-out critic invocation; callers must fail closed."""


class ReflectionRiskDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    required: bool
    reasons: tuple[str, ...] = ()


class ReflectionRiskPolicy:
    """Deterministic backend-only selection policy for critic invocation."""

    _GREETING = {"hi", "hello", "hey", "thanks", "thank you"}
    _HIGH_RISK_TERMS = {
        "billing",
        "charge",
        "charged",
        "compensation",
        "eligibility",
        "financial",
        "invoice",
        "payment",
        "refund",
        "account",
        "credit",
        "policy",
    }

    @classmethod
    def decide(
        cls,
        customer_request: str,
        findings_count: int,
        has_conflicts_or_missing_evidence: bool,
        has_action_proposal: bool,
        backend_high_risk: bool = False,
    ) -> ReflectionRiskDecision:
        normalized = customer_request.strip().lower().rstrip("!?.")
        if (not normalized or (normalized in cls._GREETING and findings_count == 0)) and not backend_high_risk:
            return ReflectionRiskDecision(required=False)

        reasons: list[str] = []
        if findings_count > 1:
            reasons.append("multiple_specialists")
        if has_conflicts_or_missing_evidence:
            reasons.append("conflicting_or_incomplete_evidence")
        if has_action_proposal:
            reasons.append("business_action_proposal")
        if backend_high_risk:
            reasons.append("backend_high_risk")
        if any(term in normalized for term in cls._HIGH_RISK_TERMS):
            reasons.append("sensitive_or_financial_or_policy_request")
        multi_intent_markers = (" and ", " also ", " plus ", "? ")
        if sum(marker in normalized for marker in multi_intent_markers) >= 2:
            reasons.append("multi_intent_request")
        return ReflectionRiskDecision(required=bool(reasons), reasons=tuple(reasons))


class ResolutionCriticAgent(BaseAgent):
    """Advisory critic with no tools, write access, or policy override authority."""

    agent_name = "resolution_critic"
    agent_version = "1.0"
    system_prompt = "phase_6f_resolution_critic_v1"

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._provider = llm_provider
        self._timeout_seconds = timeout_seconds or settings.MCSS_REFLECTION_TIMEOUT_SECONDS

    def get_capabilities(self) -> list[str]:
        return ["resolution_reflection"]

    def get_tool_allowlist(self) -> set[str]:
        return set()

    async def run(self, context: AgentContext) -> AgentResult:
        try:
            review_input = ReflectionInput.model_validate(context.payload["reflection_input"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ReflectionAgentError("valid reflection_input is required") from exc
        result = await self.critique(review_input)
        return AgentResult(
            status=AgentExecutionStatus.success,
            payload=result.model_dump(mode="json"),
            token_usage=result.token_usage,
        )

    async def critique(self, review_input: ReflectionInput) -> ReflectionResult:
        """Request strict JSON criticism from the provider using only redacted structured context."""
        safe_input = self._sanitize_input(review_input)
        critic_model = settings.MCSS_REFLECTION_MODEL or None
        request = LLMRequest(
            model=critic_model,
            messages=[
                LLMMessage(role="system", content=REFLECTION_CRITIC_SYSTEM_PROMPT),
                LLMMessage(role="user", content=json.dumps(safe_input, default=str)),
            ],
            temperature=0.0,
            max_tokens=512,
            response_format={"type": "json_object"},
            timeout=self._timeout_seconds,
        )
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                (self._provider or get_llm_provider()).chat_complete(request),
                timeout=self._timeout_seconds,
            )
            raw = json.loads(response.content)

            # Defensive sanitization of critic output to prevent minor formatting discrepancies from crashing validation
            if isinstance(raw, dict):
                if "issues" in raw and isinstance(raw["issues"], list):
                    for issue in raw["issues"]:
                        if isinstance(issue, dict) and "evidence_references" in issue:
                            if isinstance(issue["evidence_references"], list):
                                issue["evidence_references"] = [
                                    ref for ref in issue["evidence_references"]
                                    if isinstance(ref, str) and ref.startswith("ref_")
                                ]
                if "missing_information" in raw and isinstance(raw["missing_information"], list):
                    allowed_facts = {f.value for f in RequiredFact}
                    raw["missing_information"] = [
                        fact for fact in raw["missing_information"]
                        if isinstance(fact, str) and fact in allowed_facts
                    ]
                if "verdict" in raw and ("recommended_next_step" not in raw or not raw["recommended_next_step"]):
                    raw["recommended_next_step"] = "deliver" if raw["verdict"] == "approve" else raw["verdict"]

            raw["token_usage"] = {
                "prompt": response.prompt_tokens,
                "completion": response.completion_tokens,
                "total": response.total_tokens,
            }
            raw["latency_ms"] = (time.monotonic() - started) * 1000
            return ReflectionResult.model_validate(raw)
        except Exception as exc:
            logger.error("Reflection critic failed: %s: %s", type(exc).__name__, exc, exc_info=True)
            raise ReflectionAgentError("critic response was invalid or timed out") from exc

    @classmethod
    def _estimate_tokens(cls, data: Any) -> int:
        serialized = json.dumps(data, default=str)
        return max(1, math.ceil(len(serialized) / 3.5))

    @classmethod
    def _sanitize_input(cls, review_input: ReflectionInput) -> dict:
        payload = review_input.model_dump(mode="json")
        # Strip redundant nested facts and recommendations inside findings to avoid duplicate payload inflation
        if "findings" in payload and isinstance(payload["findings"], list):
            for item in payload["findings"]:
                if isinstance(item, dict):
                    item.pop("facts", None)
                    item.pop("recommendations", None)

        # Budget reflection input tokens: compact long facts if input exceeds configured budget
        max_budget = settings.MCSS_REFLECTION_MAX_INPUT_TOKENS
        if cls._estimate_tokens(payload) > max_budget and "facts" in payload and isinstance(payload["facts"], list):
            compacted_facts = []
            for fact in payload["facts"]:
                if not isinstance(fact, dict):
                    compacted_facts.append(fact)
                    continue
                fact_copy = dict(fact)
                val = fact_copy.get("field_value")
                if fact_copy.get("field_name") == "content" and isinstance(val, str) and len(val) > 400:
                    lines = [ln.strip() for ln in val.split("\n") if ln.strip()]
                    salient = [
                        ln for ln in lines
                        if re.search(r"(\$|\b\d+\b|policy|refund|return|days?|status|shipped|delivered|pending|cancel|fee|except|unless|must|cannot)", ln, re.I)
                    ]
                    if salient:
                        fact_copy["field_value"] = " ".join(salient)[:800]
                    else:
                        fact_copy["field_value"] = val[:400]
                compacted_facts.append(fact_copy)
            payload["facts"] = compacted_facts

        sanitized = mask_pii(redact_sensitive_data(payload))
        if not isinstance(sanitized, dict):
            raise ReflectionAgentError("reflection input could not be sanitized")
        return sanitized
