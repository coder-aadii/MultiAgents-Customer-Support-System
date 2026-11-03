"""Risk-based, tool-less LLM critic for evidence-grounded resolution drafts."""

from __future__ import annotations

import asyncio
import json
import time
from enum import StrEnum

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
        if self.recommended_next_step is not expected_step:
            raise ValueError("recommended_next_step must match the controlled verdict")
        if self.verdict is ReflectionVerdict.approve and (not self.grounded or not self.complete):
            raise ValueError("approval requires grounded and complete findings")
        if self.verdict is ReflectionVerdict.approve and self.issues:
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
        if normalized in cls._GREETING and findings_count == 0 and not backend_high_risk:
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
        request = LLMRequest(
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
            raw["token_usage"] = {
                "prompt": response.prompt_tokens,
                "completion": response.completion_tokens,
                "total": response.total_tokens,
            }
            raw["latency_ms"] = (time.monotonic() - started) * 1000
            return ReflectionResult.model_validate(raw)
        except Exception as exc:
            raise ReflectionAgentError("critic response was invalid or timed out") from exc

    @staticmethod
    def _sanitize_input(review_input: ReflectionInput) -> dict:
        payload = review_input.model_dump(mode="json")
        sanitized = mask_pii(redact_sensitive_data(payload))
        if not isinstance(sanitized, dict):
            raise ReflectionAgentError("reflection input could not be sanitized")
        return sanitized
