from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.actions.base import RiskLevel
from app.actions.idempotency import IdempotencyEngine, generate_idempotency_key
from app.config import settings
from app.models.action_audit_journal import (
    ActionActorType,
    ActionAuditEventType,
    ActionAuditJournal,
)
from app.models.business_action import ActionRiskLevel, ActionStatus, BusinessAction
from app.models.conversation import Conversation, ConversationStatus
from app.models.visitor import Visitor


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def sample_conversation(async_db: AsyncSession) -> Conversation:
    visitor = Visitor(name="Test Visitor", email="visitor@example.com")
    async_db.add(visitor)
    await async_db.flush()

    conv = Conversation(
        visitor_id=visitor.id,
        status=ConversationStatus.open,
    )
    async_db.add(conv)
    await async_db.commit()
    return conv


@pytest.mark.asyncio
async def test_idempotency_engine_prevents_duplicate_execution(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Test that requesting an action with identical parameters returns the existing draft and key."""
    customer_id = "cust-12345"
    action_name = "cancel_order"
    payload = {"order_id": "ORD-9912", "reason": "Accidental purchase"}

    draft1, is_new1 = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        action_name=action_name,
        risk_level=RiskLevel.MEDIUM,
        summary_message="Canceling order ORD-9912",
        required_confirmation_phrase="CONFIRM CANCEL ORD-9912",
        payload=payload,
    )
    await async_db.commit()

    assert is_new1 is True
    assert draft1.action_name == action_name
    assert draft1.required_confirmation_phrase == "CONFIRM CANCEL ORD-9912"

    # Second request with identical payload
    draft2, is_new2 = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        action_name=action_name,
        risk_level=RiskLevel.MEDIUM,
        summary_message="Canceling order ORD-9912",
        required_confirmation_phrase="CONFIRM CANCEL ORD-9912",
        payload=payload,
    )
    assert is_new2 is False
    assert draft2.action_id == draft1.action_id
    assert draft2.idempotency_key == draft1.idempotency_key


@pytest.mark.asyncio
async def test_concurrent_action_requests_acquire_atomic_lock(sample_conversation: Conversation):
    """Simulate 5 concurrent requests attempting to create the same action draft."""
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    customer_id = f"cust-concurrent-{uuid.uuid4().hex[:6]}"
    action_name = "update_address"
    payload = {"order_id": "ORD-5544", "zip": "94103"}

    async def run_create():
        async with session_factory() as session:
            draft, is_new = await IdempotencyEngine.create_or_get_draft(
                db=session,
                conversation_id=sample_conversation.id,
                customer_id=customer_id,
                action_name=action_name,
                risk_level=RiskLevel.LOW,
                summary_message="Update shipping address to 94103",
                required_confirmation_phrase="CONFIRM ADDRESS ORD-5544",
                payload=payload,
            )
            await session.commit()
            return draft.action_id, is_new

    results = await asyncio.gather(*(run_create() for _ in range(5)))
    await engine.dispose()

    action_ids = [res[0] for res in results]
    is_new_flags = [res[1] for res in results]

    # All returned action IDs must be identical
    assert len(set(action_ids)) == 1
    # Exactly one must have created it, the others reused it
    assert sum(is_new_flags) == 1


@pytest.mark.asyncio
async def test_action_state_machine_valid_transitions(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Test full audit trail logging and valid status progression."""
    draft, _ = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id="cust-audit-test",
        action_name="cancel_order",
        risk_level=RiskLevel.MEDIUM,
        summary_message="Cancel ORD-1",
        required_confirmation_phrase="CONFIRM CANCEL ORD-1",
        payload={"order_id": "ORD-1"},
    )
    await async_db.commit()

    # Query audit journal entries
    stmt = (
        select(ActionAuditJournal)
        .where(ActionAuditJournal.action_id == draft.action_id)
        .order_by(ActionAuditJournal.created_at)
    )
    res = await async_db.execute(stmt)
    entries = list(res.scalars().all())

    assert len(entries) >= 1
    assert entries[0].event_type == ActionAuditEventType.proposed.value
    assert entries[0].actor_type == ActionActorType.system.value

    # Cancel action
    cancelled_action = await IdempotencyEngine.cancel_action(
        db=async_db,
        action_id=draft.action_id,
        actor_id="cust-audit-test",
        reason="Changed mind",
    )
    await async_db.commit()

    assert cancelled_action is not None
    assert cancelled_action.status == ActionStatus.cancelled.value

    # Check updated audit trail
    res2 = await async_db.execute(stmt)
    entries2 = list(res2.scalars().all())
    assert len(entries2) == 2
    assert entries2[1].event_type == ActionAuditEventType.cancelled.value
    assert entries2[1].actor_type == ActionActorType.customer.value
    assert entries2[1].details_json.get("reason") == "Changed mind"
