"""Tests verifying Cross-User Privacy Guard & Denial (Phase 1, Item 5).

Invariants:
1. When a user queries an entity belonging to another customer (or non-existent entity),
   EntityNotFoundError must be handled gracefully without crashing the specialist.
2. The specialist must return the uniform standard security denial message.
3. No internal metadata, stack trace, or IDOR leak is permitted.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.base import AgentContext
from app.agents.specialists.business_data_agent import BusinessDataSpecialistAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.gateway.base import EntityNotFoundError


STANDARD_DENIAL_SNIPPET = "For security and privacy reasons, I can only provide information for orders and accounts directly associated with your verified login"


def make_agent_context(payload: dict, verified_customer_id: str = "cust_verified_123") -> AgentContext:
    session = SimpleNamespace(
        is_customer_authenticated=True,
        verified_customer_id=verified_customer_id,
        revoked_at=None,
    )
    return AgentContext(
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        execution_phase="specialist_test",
        payload=payload,
        db=SimpleNamespace(),
        visitor_session=session,
    )


@pytest.mark.asyncio
async def test_business_data_specialist_cross_user_privacy_denial():
    """Verify that an EntityNotFoundError from the tool gateway returns the standard denial."""
    mock_gateway = AsyncMock()
    mock_gateway.execute_get_entity.side_effect = EntityNotFoundError(
        "Entity 'order:ORD-OTHER-USER' not found or belongs to another customer"
    )

    agent = BusinessDataSpecialistAgent(tool_gateway=mock_gateway)
    ctx = make_agent_context({"entity_type": "order", "entity_id": "ORD-OTHER-USER"})

    findings = await agent.investigate(ctx)

    assert findings.status is SpecialistStatus.completed
    assert findings.confidence == 1.0
    assert len(findings.recommendations) > 0
    assert STANDARD_DENIAL_SNIPPET in findings.recommendations[0]
    assert findings.facts == ()


@pytest.mark.asyncio
async def test_business_data_specialist_fuzz_cross_user_probes():
    """Fuzz various cross-user and malicious IDOR entity_id payloads."""
    mock_gateway = AsyncMock()
    mock_gateway.execute_get_entity.side_effect = EntityNotFoundError("Access Denied")

    agent = BusinessDataSpecialistAgent(tool_gateway=mock_gateway)

    adversarial_ids = [
        "ORD-0000",
        "ORD-99999999",
        "../admin",
        "' OR 1=1 --",
        "cust_other_user",
        "../../etc/passwd",
        "\x00nullbyte",
    ]

    for probe_id in adversarial_ids:
        ctx = make_agent_context({"entity_type": "order", "entity_id": probe_id})
        findings = await agent.investigate(ctx)
        assert findings.status is SpecialistStatus.completed
        assert any(STANDARD_DENIAL_SNIPPET in rec for rec in findings.recommendations)

