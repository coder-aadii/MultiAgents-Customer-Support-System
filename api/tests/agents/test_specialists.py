from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.agents.base import AgentContext
from app.agents.specialists.action_agent import ActionProposalSpecialistAgent
from app.agents.specialists.business_data_agent import BusinessDataSpecialistAgent
from app.agents.specialists.knowledge_agent import KnowledgeSpecialistAgent
from app.agents.specialists.resolution_agent import ResolutionSynthesisAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.schemas.evidence import VerifiedFact


def context(payload: dict, authenticated: bool = True, db: object | None = None) -> AgentContext:
    session = SimpleNamespace(
        is_customer_authenticated=authenticated,
        verified_customer_id="cust_verified" if authenticated else None,
    )
    return AgentContext(
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        execution_phase="specialist_test",
        payload=payload,
        db=db,  # type: ignore[arg-type]
        visitor_session=session,  # type: ignore[arg-type]
    )


def test_knowledge_specialist_isolation() -> None:
    agent = KnowledgeSpecialistAgent()

    assert agent.get_tool_allowlist() == {"retrieve_knowledge_chunks"}
    assert "execute_get_entity" not in agent.get_tool_allowlist()
    assert "execute_list_entities" not in agent.get_tool_allowlist()


@pytest.mark.asyncio
async def test_business_data_specialist_ial_enforcement() -> None:
    agent = BusinessDataSpecialistAgent()

    findings = await agent.investigate(context({"entity_type": "order"}, authenticated=False))

    assert findings.status is SpecialistStatus.authentication_required
    assert findings.failure_reason == "IAL_2_REQUIRED"


@pytest.mark.asyncio
async def test_action_proposal_precondition_check() -> None:
    agent = ActionProposalSpecialistAgent()

    findings = await agent.investigate(
        context(
            {
                "action_name": "cancel_order",
                "parameters": {"order_id": "ORD-9999", "current_order_status": "shipped"},
            },
            db=SimpleNamespace(),
        )
    )

    assert findings.status is SpecialistStatus.ineligible
    assert findings.action_draft is None
    assert findings.failure_reason == "ACTION_PRECONDITION_FAILED"


@pytest.mark.asyncio
async def test_resolution_agent_synthesizes_multiple_findings() -> None:
    findings = [
        SpecialistFindings(
            specialist="business_data_specialist",
            status=SpecialistStatus.completed,
            facts=(
                VerifiedFact(
                    entity_type="order",
                    entity_id="ORD-88219",
                    field_name="status",
                    field_value="delayed",
                    source_evidence_id="ref_order_1",
                ),
            ),
            evidence_keys=("ref_order_1",),
            confidence=1.0,
            recommendations=("Order ORD-88219 is delayed.",),
        ),
        SpecialistFindings(
            specialist="billing_specialist",
            status=SpecialistStatus.completed,
            confidence=1.0,
            recommendations=("No refund has been initiated.",),
        ),
        SpecialistFindings(
            specialist="knowledge_specialist",
            status=SpecialistStatus.completed,
            confidence=0.9,
            recommendations=("The delivery policy has been reviewed.",),
        ),
    ]

    result = await ResolutionSynthesisAgent().investigate(
        context({"findings": [finding.model_dump(mode="json") for finding in findings]})
    )

    assert result.status is SpecialistStatus.completed
    assert result.recommendations == (
        "Order ORD-88219 is delayed. No refund has been initiated. The delivery policy has been reviewed.",
    )
