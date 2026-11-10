"""Fail-closed multi-agent resolution coordination and policy grounding."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentBudget, AgentContext
from app.agents.audit import record_agent_execution
from app.config import settings
from app.agents.orchestration.aggregator import AggregatedEvidence
from app.agents.reflection import (
    ReflectionAgentError,
    ReflectionInput,
    ReflectionResult,
    ReflectionRiskPolicy,
    ReflectionVerdict,
    ResolutionCriticAgent,
)
from app.agents.specialists.resolution_agent import ResolutionSynthesisAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.models.handoff import HandoffReason
from app.schemas.evidence import EvidenceReference
from app.services.assistant_service import create_or_reuse_handoff
from app.services.policy_validator import PolicyValidator


class ResolutionStatus(StrEnum):
    ready = "ready"
    replan_required = "replan_required"
    clarification_required = "clarification_required"
    handoff_required = "handoff_required"


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    status: ResolutionStatus
    response_text: str | None
    citations: tuple[EvidenceReference, ...]
    reason: str | None = None
    reflection: ReflectionResult | None = None
    reflection_reasons: tuple[str, ...] = ()


class MultiAgentSynthesisCoordinator:
    """Produces customer text only after evidence aggregation and deterministic grounding."""

    def __init__(
        self,
        resolution_agent: ResolutionSynthesisAgent | None = None,
        reflection_agent: ResolutionCriticAgent | None = None,
        reflection_enabled: bool | None = None,
        max_revisions: int | None = None,
        max_supplemental_investigations: int | None = None,
        revision_generator: Callable[[str, ReflectionResult], Awaitable[str]] | None = None,
        supplemental_investigation: Callable[
            [tuple[str, ...]], Awaitable[tuple[list[SpecialistFindings], AggregatedEvidence]]
        ]
        | None = None,
    ) -> None:
        self._resolution_agent = resolution_agent or ResolutionSynthesisAgent()
        self._reflection_agent = reflection_agent or ResolutionCriticAgent()
        from app.config import settings

        self._reflection_enabled = settings.MCSS_REFLECTION_ENABLED if reflection_enabled is None else reflection_enabled
        self._max_revisions = settings.MCSS_REFLECTION_MAX_REVISIONS if max_revisions is None else max_revisions
        self._max_supplemental_investigations = (
            settings.MCSS_REFLECTION_MAX_SUPPLEMENTAL_INVESTIGATIONS
            if max_supplemental_investigations is None
            else max_supplemental_investigations
        )
        self._revision_generator = revision_generator
        self._supplemental_investigation = supplemental_investigation

    async def synthesize(
        self,
        context: AgentContext,
        findings: list[SpecialistFindings],
        evidence: AggregatedEvidence,
        db: AsyncSession | None = None,
        backend_high_risk: bool = False,
        supplemental_investigation_authorized: bool = False,
    ) -> ResolutionResult:
        if evidence.replan_required:
            return ResolutionResult(
                status=ResolutionStatus.replan_required,
                response_text=None,
                citations=evidence.citations,
                reason=evidence.replan_reason,
            )
        if evidence.conflicts or evidence.missing_evidence_specialists:
            return await self._fail_closed(
                context,
                evidence,
                "evidence_conflict_or_missing_after_replan_budget",
                db,
            )

        draft = await self._synthesize_draft(context, findings, evidence)
        if isinstance(draft, ResolutionResult):
            return await self._fail_closed(context, evidence, draft.reason or "resolution_synthesis_failed", db)

        customer_request = str(context.payload.get("user_message", "")).strip()
        risk = ReflectionRiskPolicy.decide(
            customer_request=customer_request,
            findings_count=len(findings),
            has_conflicts_or_missing_evidence=bool(
                evidence.conflicts or evidence.missing_evidence_specialists
            ),
            has_action_proposal=any(finding.action_draft is not None for finding in findings),
            backend_high_risk=backend_high_risk,
        )
        if not self._reflection_enabled or not risk.required:
            return ResolutionResult(
                status=ResolutionStatus.ready,
                response_text=draft,
                citations=evidence.citations,
                reflection_reasons=risk.reasons,
            )
        if not customer_request:
            return await self._fail_closed(context, evidence, "reflection_request_missing", db)

        revision_count = 0
        investigation_count = 0
        active_findings = findings
        active_evidence = evidence
        while True:
            try:
                reflection_input = ReflectionInput(
                        customer_request=customer_request,
                        draft_response=draft,
                        findings=[
                            finding.model_dump(mode="json", exclude={"facts", "recommendations"})
                            for finding in active_findings
                        ],
                        facts=active_evidence.facts,
                        citations=active_evidence.citations,
                    )
                reflection_budget = AgentBudget(
                    max_timeout_seconds=settings.MCSS_REFLECTION_TIMEOUT_SECONDS,
                    max_execution_tokens=context.budget.max_execution_tokens,
                    max_tool_calls=context.budget.max_tool_calls,
                )
                reflection_context = AgentContext(
                    tenant_id=context.tenant_id,
                    conversation_id=context.conversation_id,
                    execution_phase="reflection",
                    payload={"reflection_input": reflection_input.model_dump(mode="json")},
                    verified_customer_id=context.verified_customer_id,
                    budget=reflection_budget,
                    db=context.db,
                    visitor_session=context.visitor_session,
                )
                started_at = monotonic()
                if hasattr(self._reflection_agent, "execute"):
                    critic_result = await self._reflection_agent.execute(reflection_context)
                    reflection = ReflectionResult.model_validate(critic_result.payload)
                else:
                    # Compatibility for deterministic test doubles; production
                    # critic implementations are BaseAgent instances.
                    reflection = await self._reflection_agent.critique(reflection_input)
                    critic_result = None
                if db is not None and critic_result is not None:
                    await record_agent_execution(
                        db, agent=self._reflection_agent, context=reflection_context,
                        started_at=started_at, result=critic_result,
                    )
                    await db.flush()
            except (ReflectionAgentError, ValueError, RuntimeError) as exc:
                import logging
                logging.getLogger(__name__).warning("Reflection execution failed: %s: %s", type(exc).__name__, exc, exc_info=True)
                return await self._fail_closed(
                    context, active_evidence, "mandatory_reflection_failed", db
                )

            if reflection.verdict is ReflectionVerdict.approve:
                grounded, reason = PolicyValidator.validate_response_grounding(
                    draft, list(active_evidence.facts)
                )
                if not grounded:
                    return await self._fail_closed(
                        context, active_evidence, f"final_policy_grounding_failed:{reason}", db
                    )
                return ResolutionResult(
                    status=ResolutionStatus.ready,
                    response_text=draft,
                    citations=active_evidence.citations,
                    reflection=reflection,
                    reflection_reasons=risk.reasons,
                )

            if reflection.verdict is ReflectionVerdict.revise:
                if revision_count >= self._max_revisions:
                    return await self._fail_closed(
                        context, active_evidence, "reflection_revision_budget_exhausted", db
                    )
                revision_count += 1
                try:
                    draft = (
                        await self._revision_generator(draft, reflection)
                        if self._revision_generator is not None
                        else await self._default_revision(context, active_findings, active_evidence, reflection, draft)
                    )
                except Exception:
                    return await self._fail_closed(
                        context, active_evidence, "reflection_revision_failed", db
                    )
                if not self._is_deterministically_grounded(draft, active_evidence):
                    return await self._fail_closed(
                        context, active_evidence, "revised_draft_policy_grounding_failed", db
                    )
                continue

            if reflection.verdict is ReflectionVerdict.investigate:
                if (
                    investigation_count >= self._max_supplemental_investigations
                    or not supplemental_investigation_authorized
                    or self._supplemental_investigation is None
                ):
                    return await self._fail_closed(
                        context, active_evidence, "supplemental_investigation_not_authorized", db
                    )
                investigation_count += 1
                try:
                    active_findings, active_evidence = await self._supplemental_investigation(
                        tuple(item.value for item in reflection.missing_information)
                    )
                except Exception:
                    return await self._fail_closed(
                        context, active_evidence, "supplemental_investigation_failed", db
                    )
                if active_evidence.conflicts or active_evidence.missing_evidence_specialists:
                    return await self._fail_closed(
                        context, active_evidence, "supplemental_evidence_insufficient", db
                    )
                draft = await self._default_revision(context, active_findings, active_evidence)
                if not self._is_deterministically_grounded(draft, active_evidence):
                    return await self._fail_closed(
                        context, active_evidence, "supplemental_draft_policy_grounding_failed", db
                    )
                continue

            if reflection.verdict is ReflectionVerdict.clarify:
                return ResolutionResult(
                    status=ResolutionStatus.clarification_required,
                    response_text=self._clarification_text(tuple(item.value for item in reflection.missing_information)),
                    citations=active_evidence.citations,
                    reason="reflection_requested_clarification",
                    reflection=reflection,
                    reflection_reasons=risk.reasons,
                )
            return await self._fail_closed(
                context, active_evidence, "reflection_escalated", db, reflection
            )

    async def _synthesize_draft(
        self,
        context: AgentContext,
        findings: list[SpecialistFindings],
        evidence: AggregatedEvidence,
    ) -> str | ResolutionResult:
        resolution_context = AgentContext(
            tenant_id=context.tenant_id,
            conversation_id=context.conversation_id,
            execution_phase="resolution_synthesis",
            payload={"findings": [finding.model_dump(mode="json") for finding in findings]},
            verified_customer_id=context.verified_customer_id,
            budget=context.budget,
            db=context.db,
            visitor_session=context.visitor_session,
        )
        started_at = monotonic()
        try:
            resolution_result = await self._resolution_agent.execute(resolution_context)
            synthesized = SpecialistFindings.model_validate(resolution_result.payload)
            if context.db is not None:
                await record_agent_execution(
                    context.db, agent=self._resolution_agent, context=resolution_context,
                    started_at=started_at, result=resolution_result,
                )
                await context.db.flush()
        except Exception:
            return ResolutionResult(
                status=ResolutionStatus.handoff_required,
                response_text=None,
                citations=evidence.citations,
                reason="resolution_synthesis_failed",
            )
        if synthesized.status is not SpecialistStatus.completed or not synthesized.recommendations:
            failure_reason = synthesized.failure_reason or "resolution_synthesis_failed"
            if failure_reason.startswith("POLICY_GROUNDING_FAILED"):
                failure_reason = "policy_grounding_failed"
            return ResolutionResult(
                status=ResolutionStatus.handoff_required,
                response_text=None,
                citations=evidence.citations,
                reason=failure_reason,
            )
        draft = synthesized.recommendations[0]
        if not self._is_deterministically_grounded(draft, evidence):
            return ResolutionResult(
                status=ResolutionStatus.handoff_required,
                response_text=None,
                citations=evidence.citations,
                reason="policy_grounding_failed",
            )
        return draft

    async def _default_revision(
        self,
        context: AgentContext,
        findings: list[SpecialistFindings],
        evidence: AggregatedEvidence,
        reflection: ReflectionResult | None = None,
        original_draft: str | None = None,
    ) -> str:
        if reflection and reflection.issues and original_draft:
            try:
                from app.providers import get_llm_provider, LLMRequest, LLMMessage
                provider = get_llm_provider()
                facts_text = "\n".join(
                    f"- {f.field_name}: {f.field_value}"
                    for f in evidence.facts[:8]
                )
                issues_text = "\n".join(
                    f"- {issue.issue_type}: {issue.description}"
                    for issue in reflection.issues
                )
                prompt = (
                    f"Revise the customer draft response to resolve these feedback issues:\n"
                    f"{issues_text}\n\n"
                    f"Verified Facts:\n{facts_text}\n\n"
                    f"Current Draft:\n{original_draft}\n\n"
                    f"Provide only the revised plain text response, strictly grounded in the verified facts without inventing any details."
                )
                req = LLMRequest(
                    messages=[
                        LLMMessage(role="system", content="You are a customer support resolution reviser. Return ONLY the plain text revised customer response."),
                        LLMMessage(role="user", content=prompt),
                    ],
                    temperature=0.0,
                    max_tokens=512,
                    timeout=settings.MCSS_REFLECTION_TIMEOUT_SECONDS,
                )
                resp = await provider.chat_complete(req)
                revised = resp.content.strip()
                if revised and self._is_deterministically_grounded(revised, evidence):
                    return revised
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Default revision generator failed: %s", e)

        draft = await self._synthesize_draft(context, findings, evidence)
        if isinstance(draft, ResolutionResult):
            raise RuntimeError("revision synthesis failed")
        return draft

    @staticmethod
    def _is_deterministically_grounded(draft: str, evidence: AggregatedEvidence) -> bool:
        grounded, _ = PolicyValidator.validate_response_grounding(draft, list(evidence.facts))
        return grounded

    @staticmethod
    def _clarification_text(missing_information: tuple[str, ...]) -> str:
        prompts = {
            "order_id": "the order number",
            "tracking_number": "the tracking number",
            "refund_reference": "the refund reference",
            "account_verification": "account verification",
            "requested_action": "the action you would like us to take",
            "policy_scope": "which policy detail you need clarified",
        }
        requested = [prompts[item] for item in missing_information if item in prompts]
        if not requested:
            return "Could you clarify the detail needed to complete this request?"
        return f"Could you provide {', '.join(requested)} so I can complete this request?"

    async def _fail_closed(
        self,
        context: AgentContext,
        evidence: AggregatedEvidence,
        reason: str,
        db: AsyncSession | None,
        reflection: ReflectionResult | None = None,
    ) -> ResolutionResult:
        if db is not None:
            await create_or_reuse_handoff(
                db,
                conversation_id=context.conversation_id,
                reason=HandoffReason.policy,
                summary=f"Multi-agent resolution halted: {reason}"[:500],
            )
            await db.flush()
        return ResolutionResult(
            status=ResolutionStatus.handoff_required,
            response_text=None,
            citations=evidence.citations,
            reason=reason,
            reflection=reflection,
        )
