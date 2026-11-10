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

    assert findings.status is SpecialistStatus.completed
    assert "log in to your account first" in findings.recommendations[0]


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


@pytest.mark.asyncio
async def test_knowledge_specialist_explains_article_content(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.schemas.retrieval import RetrievalResponse, RetrievalResultItem

    chunk_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    fake_chunk = RetrievalResultItem(
        chunk_id=chunk_id,
        document_id=doc_id,
        document_title="01-order-statuses",
        rank=1,
        score=0.88,
        content="# Order Statuses\n## Supported states\n- shipped: order was recorded as shipped; parcel in transit.",
        token_estimate=50,
        start_char=0,
        end_char=100,
    )

    async def fake_retrieve(*args: object, **kwargs: object) -> RetrievalResponse:
        return RetrievalResponse(
            query="what does shipped mean",
            results=[fake_chunk],
            total_found=1,
        )

    monkeypatch.setattr(
        "app.agents.specialists.knowledge_agent.retrieve_knowledge_chunks",
        fake_retrieve,
    )

    agent = KnowledgeSpecialistAgent()
    findings = await agent.investigate(
        context({"query": "what does shipped mean"}, db=SimpleNamespace())
    )

    assert findings.status is SpecialistStatus.completed
    assert len(findings.recommendations) == 1
    explanation = findings.recommendations[0]
    assert "Review the published policy" not in explanation
    assert "shipped" in explanation
    assert any(fact.field_name == "content" for fact in findings.facts)
