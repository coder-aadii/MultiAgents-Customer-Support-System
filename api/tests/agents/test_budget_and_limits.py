from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from app.agents.base import AgentBudget, AgentBudgetExceededError, AgentContext, AgentResult, BaseAgent
from app.agents.orchestration.aggregator import EvidenceAggregator
from app.agents.orchestration.synthesizer import MultiAgentSynthesisCoordinator, ResolutionStatus
from app.agents.reflection import ResolutionCriticAgent
from app.agents.schemas import AgentTokenUsage
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.providers import FakeLLMProvider, ProviderConnectionError
from app.schemas.evidence import VerifiedFact


class HungryAgent(BaseAgent):
    async def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(token_usage=AgentTokenUsage(prompt=4, completion=4, total=8))

    def get_capabilities(self) -> list[str]:
        return ["test"]

    def get_tool_allowlist(self) -> set[str]:
        return set()


def _context() -> AgentContext:
    return AgentContext(
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        execution_phase="budget_test",
        payload={"user_message": "Can I get a refund?"},
    )


def _findings() -> list[SpecialistFindings]:
    return [
        SpecialistFindings(
            specialist="billing_specialist",
            status=SpecialistStatus.completed,
            facts=(
                VerifiedFact(
                    entity_type="refund",
                    entity_id="REF-00001",
                    field_name="status",
                    field_value="not initiated",
                    source_evidence_id="refund:1",
                ),
            ),
            evidence_keys=("refund:1",),
            recommendations=("Refund REF-00001 has not been initiated.",),
        )
    ]


@pytest.mark.asyncio
async def test_agent_token_budget_stress_limit() -> None:
    with pytest.raises(AgentBudgetExceededError, match="token budget"):
        await HungryAgent().execute(replace(_context(), budget=AgentBudget(max_execution_tokens=7)))


@pytest.mark.asyncio
async def test_provider_failure_graceful_handoff_and_reflection_token_cap() -> None:
    context = _context()
    findings = _findings()
    evidence = EvidenceAggregator().aggregate(context.tenant_id, context.conversation_id, findings)
    provider = FakeLLMProvider()
    provider.queue_error(ProviderConnectionError("simulated 503"))

    result = await MultiAgentSynthesisCoordinator(
        reflection_agent=ResolutionCriticAgent(provider), reflection_enabled=True
    ).synthesize(context, findings, evidence)

    assert result.status is ResolutionStatus.handoff_required
    assert result.reason == "mandatory_reflection_failed"
    assert provider.calls[0].max_tokens == 512
