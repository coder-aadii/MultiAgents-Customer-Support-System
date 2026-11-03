from __future__ import annotations

import uuid

import pytest

from app.agents.base import AgentContext
from app.agents.reflection import (
    ReflectionIssue,
    ReflectionIssueType,
    ReflectionInput,
    ReflectionNextStep,
    ReflectionResult,
    ReflectionVerdict,
    ResolutionCriticAgent,
)
from app.agents.orchestration.aggregator import EvidenceAggregator
from app.agents.orchestration.synthesizer import (
    MultiAgentSynthesisCoordinator,
    ResolutionStatus,
)
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.schemas.evidence import VerifiedFact
from app.providers import FakeLLMProvider


def context(user_message: str = "") -> AgentContext:
    return AgentContext(
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        execution_phase="resolution_test",
        payload={"user_message": user_message},
    )


def fact(
    entity_type: str, entity_id: str, field_name: str, value: str | int | float, source: str
) -> VerifiedFact:
    return VerifiedFact(
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=field_name,
        field_value=value,
        source_evidence_id=source,
    )


class StubCritic:
    def __init__(self, result: ReflectionResult) -> None:
        self.result = result
        self.calls = 0

    async def critique(self, _input) -> ReflectionResult:
        self.calls += 1
        return self.result


def review(
    verdict: ReflectionVerdict,
    *,
    grounded: bool = True,
    complete: bool = True,
    missing_information: tuple = (),
    issues: tuple = (),
) -> ReflectionResult:
    step = ReflectionNextStep.deliver if verdict is ReflectionVerdict.approve else ReflectionNextStep(verdict.value)
    return ReflectionResult(
        verdict=verdict,
        grounded=grounded,
        complete=complete,
        issues=issues,
        recommended_next_step=step,
        missing_information=missing_information,
    )


def order_findings() -> list[SpecialistFindings]:
    return [
        SpecialistFindings(
            specialist="business_data_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("order", "ORD-88219", "status", "processing", "order:88219"),),
            evidence_keys=("order:88219",),
            confidence=1.0,
            recommendations=("Order ORD-88219 is processing.",),
        )
    ]


def test_evidence_aggregator_conflict_detection() -> None:
    invocation_context = context()
    findings = [
        SpecialistFindings(
            specialist="logistics_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("shipment", "TRK-99012", "status", "delivered", "shipment:1"),),
            evidence_keys=("shipment:1",),
        ),
        SpecialistFindings(
            specialist="business_data_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("shipment", "TRK-99012", "status", "stalled", "shipment:2"),),
            evidence_keys=("shipment:2",),
        ),
    ]

    evidence = EvidenceAggregator().aggregate(
        invocation_context.tenant_id, invocation_context.conversation_id, findings
    )

    assert evidence.replan_required is True
    assert evidence.replan_reason == "evidence_conflict"
    assert evidence.conflicts[0].values == ("delivered", "stalled")


@pytest.mark.asyncio
async def test_synthesizer_cites_all_relevant_evidence() -> None:
    invocation_context = context()
    findings = [
        SpecialistFindings(
            specialist="business_data_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("order", "ORD-88219", "status", "delayed", "order:88219"),),
            evidence_keys=("order:88219",),
            confidence=1.0,
            recommendations=("Order ORD-88219 is delayed.",),
        ),
        SpecialistFindings(
            specialist="knowledge_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("knowledge_chunk", "chunk-402", "title", "Express delivery policy", "chunk:402"),),
            evidence_keys=("chunk:402",),
            confidence=0.9,
            recommendations=("The express delivery policy has been reviewed.",),
        ),
    ]
    evidence = EvidenceAggregator().aggregate(
        invocation_context.tenant_id, invocation_context.conversation_id, findings
    )

    result = await MultiAgentSynthesisCoordinator().synthesize(
        invocation_context, findings, evidence
    )

    assert result.status is ResolutionStatus.ready
    assert len(result.citations) == 2
    assert all(citation.evidence_id.startswith("ref_") for citation in result.citations)
    assert "order:88219" not in str(result.citations)


@pytest.mark.asyncio
async def test_policy_validator_catches_synthesizer_hallucination() -> None:
    invocation_context = context()
    findings = [
        SpecialistFindings(
            specialist="billing_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("order", "ORD-88219", "shipping_fee", 19.99, "order:fee"),),
            evidence_keys=("order:fee",),
            confidence=1.0,
            recommendations=("The shipping fee was $99.99.",),
        )
    ]
    evidence = EvidenceAggregator().aggregate(
        invocation_context.tenant_id, invocation_context.conversation_id, findings
    )

    result = await MultiAgentSynthesisCoordinator().synthesize(
        invocation_context, findings, evidence
    )

    assert result.status is ResolutionStatus.handoff_required
    assert result.reason is not None
    assert result.reason.startswith("policy_grounding_failed")


@pytest.mark.asyncio
async def test_end_to_end_demonstration_scenario() -> None:
    """Delayed order + express fee + missing refund benchmark from the Phase 6 specification."""
    invocation_context = context()
    findings = [
        SpecialistFindings(
            specialist="business_data_specialist",
            status=SpecialistStatus.completed,
            facts=(
                fact("order", "ORD-88219", "shipping_fee", 19.99, "order:88219"),
                fact("order", "ORD-88219", "status", "delayed", "order:88219"),
            ),
            evidence_keys=("order:88219",),
            confidence=1.0,
            recommendations=("Order ORD-88219 is delayed and has a $19.99 express shipping fee.",),
        ),
        SpecialistFindings(
            specialist="logistics_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("shipment", "TRK-99012", "status", "stalled", "shipment:99012"),),
            evidence_keys=("shipment:99012",),
            confidence=1.0,
            recommendations=("Shipment TRK-99012 is stalled at the carrier sorting hub.",),
        ),
        SpecialistFindings(
            specialist="billing_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("refund", "REF-00001", "status", "not initiated", "refund:missing"),),
            evidence_keys=("refund:missing",),
            confidence=1.0,
            recommendations=("No refund has been initiated for this order.",),
        ),
        SpecialistFindings(
            specialist="knowledge_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("knowledge_chunk", "chunk-402", "title", "Express delivery policy", "chunk:402"),),
            evidence_keys=("chunk:402",),
            confidence=0.9,
            recommendations=("The express delivery policy has been reviewed.",),
        ),
    ]
    evidence = EvidenceAggregator().aggregate(
        invocation_context.tenant_id, invocation_context.conversation_id, findings
    )

    result = await MultiAgentSynthesisCoordinator().synthesize(
        invocation_context, findings, evidence
    )

    assert result.status is ResolutionStatus.ready
    assert result.response_text is not None
    assert "ORD-88219" in result.response_text
    assert "TRK-99012" in result.response_text
    assert "$19.99" in result.response_text
    assert len(result.citations) == 4


@pytest.mark.asyncio
async def test_simple_faq_skips_reflection() -> None:
    invocation_context = context("What are your shipping hours?")
    findings = [
        SpecialistFindings(
            specialist="knowledge_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("knowledge_chunk", "chunk-1", "title", "Shipping hours", "chunk:1"),),
            evidence_keys=("chunk:1",),
            recommendations=("Shipping hours are listed in the published policy.",),
        )
    ]
    evidence = EvidenceAggregator().aggregate(invocation_context.tenant_id, invocation_context.conversation_id, findings)
    critic = StubCritic(review(ReflectionVerdict.approve))

    result = await MultiAgentSynthesisCoordinator(
        reflection_agent=critic, reflection_enabled=True
    ).synthesize(invocation_context, findings, evidence)

    assert result.status is ResolutionStatus.ready
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_verified_deterministic_order_status_skips_reflection() -> None:
    invocation_context = context("Where is my order?")
    findings = order_findings()
    evidence = EvidenceAggregator().aggregate(invocation_context.tenant_id, invocation_context.conversation_id, findings)
    critic = StubCritic(review(ReflectionVerdict.approve))

    result = await MultiAgentSynthesisCoordinator(
        reflection_agent=critic, reflection_enabled=True
    ).synthesize(invocation_context, findings, evidence)

    assert result.status is ResolutionStatus.ready
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_multi_intent_and_financial_requests_trigger_reflection() -> None:
    invocation_context = context("My order is delayed and my refund is missing.")
    findings = order_findings() + [
        SpecialistFindings(
            specialist="billing_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("refund", "REF-00001", "status", "not initiated", "refund:1"),),
            evidence_keys=("refund:1",),
            recommendations=("Refund REF-00001 has not been initiated.",),
        )
    ]
    evidence = EvidenceAggregator().aggregate(invocation_context.tenant_id, invocation_context.conversation_id, findings)
    critic = StubCritic(review(ReflectionVerdict.approve))

    result = await MultiAgentSynthesisCoordinator(
        reflection_agent=critic, reflection_enabled=True
    ).synthesize(invocation_context, findings, evidence)

    assert result.status is ResolutionStatus.ready
    assert critic.calls == 1
    assert "multiple_specialists" in result.reflection_reasons
    assert "sensitive_or_financial_or_policy_request" in result.reflection_reasons


@pytest.mark.asyncio
async def test_critic_missing_answer_returns_targeted_clarification() -> None:
    invocation_context = context("Can you check my order?")
    findings = order_findings()
    evidence = EvidenceAggregator().aggregate(invocation_context.tenant_id, invocation_context.conversation_id, findings)
    critic = StubCritic(
        review(ReflectionVerdict.clarify, complete=False, missing_information=("tracking_number",))
    )

    result = await MultiAgentSynthesisCoordinator(
        reflection_agent=critic, reflection_enabled=True
    ).synthesize(invocation_context, findings, evidence, backend_high_risk=True)

    assert result.status is ResolutionStatus.clarification_required
    assert result.response_text is not None
    assert "tracking number" in result.response_text


@pytest.mark.asyncio
async def test_critic_approval_cannot_override_policy_validator_rejection() -> None:
    invocation_context = context("Please explain this refund.")
    findings = [
        SpecialistFindings(
            specialist="billing_specialist",
            status=SpecialistStatus.completed,
            facts=(fact("order", "ORD-88219", "shipping_fee", 19.99, "order:fee"),),
            evidence_keys=("order:fee",),
            recommendations=("The shipping fee was $99.99.",),
        )
    ]
    evidence = EvidenceAggregator().aggregate(invocation_context.tenant_id, invocation_context.conversation_id, findings)
    critic = StubCritic(review(ReflectionVerdict.approve))

    result = await MultiAgentSynthesisCoordinator(
        reflection_agent=critic, reflection_enabled=True
    ).synthesize(invocation_context, findings, evidence)

    assert result.status is ResolutionStatus.handoff_required
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_invalid_critic_json_and_timeout_fail_closed() -> None:
    invocation_context = context("Can I get a refund?")
    findings = order_findings()
    evidence = EvidenceAggregator().aggregate(invocation_context.tenant_id, invocation_context.conversation_id, findings)
    provider = FakeLLMProvider()
    provider.queue_response("not json")
    invalid_critic = ResolutionCriticAgent(provider, timeout_seconds=0.1)

    invalid_result = await MultiAgentSynthesisCoordinator(
        reflection_agent=invalid_critic, reflection_enabled=True
    ).synthesize(invocation_context, findings, evidence)
    assert invalid_result.status is ResolutionStatus.handoff_required
    assert invalid_result.reason == "mandatory_reflection_failed"

    slow_provider = FakeLLMProvider()
    slow_provider.delay_seconds = 0.02
    timeout_critic = ResolutionCriticAgent(slow_provider, timeout_seconds=0.001)
    timeout_result = await MultiAgentSynthesisCoordinator(
        reflection_agent=timeout_critic, reflection_enabled=True
    ).synthesize(invocation_context, findings, evidence)
    assert timeout_result.status is ResolutionStatus.handoff_required


@pytest.mark.asyncio
async def test_revision_and_supplemental_investigation_budgets_fail_closed() -> None:
    invocation_context = context("I need a refund and an order update.")
    findings = order_findings()
    evidence = EvidenceAggregator().aggregate(invocation_context.tenant_id, invocation_context.conversation_id, findings)
    revision_critic = StubCritic(review(ReflectionVerdict.revise, complete=False))

    revision_result = await MultiAgentSynthesisCoordinator(
        reflection_agent=revision_critic, reflection_enabled=True, max_revisions=1
    ).synthesize(invocation_context, findings, evidence)
    assert revision_result.status is ResolutionStatus.handoff_required
    assert revision_result.reason == "reflection_revision_budget_exhausted"

    investigate_critic = StubCritic(
        review(ReflectionVerdict.investigate, complete=False, missing_information=("refund_reference",))
    )
    investigate_result = await MultiAgentSynthesisCoordinator(
        reflection_agent=investigate_critic, reflection_enabled=True
    ).synthesize(invocation_context, findings, evidence, supplemental_investigation_authorized=False)
    assert investigate_result.status is ResolutionStatus.handoff_required
    assert investigate_result.reason == "supplemental_investigation_not_authorized"


def test_cross_tenant_evidence_references_are_not_reusable() -> None:
    findings = order_findings()
    first_context = context("Where is my order?")
    second_context = context("Where is my order?")
    first = EvidenceAggregator().aggregate(first_context.tenant_id, first_context.conversation_id, findings)
    second = EvidenceAggregator().aggregate(second_context.tenant_id, second_context.conversation_id, findings)

    assert first.citations[0].evidence_id != second.citations[0].evidence_id
    assert "order:88219" not in first.citations[0].evidence_id


@pytest.mark.asyncio
async def test_critic_reports_unsupported_conflict_and_pending_action_issues() -> None:
    provider = FakeLLMProvider()
    provider.queue_response(
        {
            "verdict": "revise",
            "grounded": False,
            "complete": False,
            "issues": [
                {
                    "issue_type": "unsupported_claim",
                    "description": "The refund conclusion is not supported.",
                    "evidence_references": ["ref_a"],
                },
                {
                    "issue_type": "conflicting_evidence",
                    "description": "Specialist statuses conflict.",
                    "evidence_references": ["ref_b"],
                },
                {
                    "issue_type": "action_state",
                    "description": "A proposed action is described as completed.",
                    "evidence_references": ["ref_c"],
                },
            ],
            "recommended_next_step": "revise",
            "missing_information": [],
        }
    )
    critic = ResolutionCriticAgent(provider)

    result = await critic.critique(
        ReflectionInput(
            customer_request="Can you refund my delayed order?",
            draft_response="Your refund was completed.",
            findings=[],
            facts=(),
            citations=(),
        )
    )

    assert {issue.issue_type for issue in result.issues} == {
        ReflectionIssueType.unsupported_claim,
        ReflectionIssueType.conflicting_evidence,
        ReflectionIssueType.action_state,
    }
    assert result.token_usage.total > 0
    assert result.latency_ms >= 0
