from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.actions.base import RiskLevel
from app.actions.executor import ActionExecutor
from app.actions.idempotency import IdempotencyEngine
from app.actions.policy import ActionPolicyGate, ConfirmationDecision
from app.actions.registry import ActionRegistry, OrderCancellationAction
from app.config import settings
from app.models.action_audit_journal import (
    ActionActorType,
    ActionAuditEventType,
    ActionAuditJournal,
)
from app.models.business_action import ActionStatus, BusinessAction
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
    visitor = Visitor(name="Low Risk Tester", email="lowrisk@example.com")
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
async def test_cancel_order_action_executes_mutation(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Test that valid order cancellation executes and produces a confirmation code."""
    customer_id = "cust-low-1"
    order_id = "ORD-3301"

    action_def = ActionRegistry.get("cancel_order")
    assert action_def is not None

    # 1. Validate preconditions
    is_valid, msg, params = await action_def.validate_preconditions(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        parameters={"order_id": order_id, "current_order_status": "processing"},
    )
    assert is_valid is True
    summary, phrase = action_def.generate_confirmation_phrase(customer_id, params)

    # 2. Draft action
    draft, _ = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        action_name="cancel_order",
        risk_level=action_def.risk_level,
        summary_message=summary,
        required_confirmation_phrase=phrase,
        payload=params,
    )
    await async_db.commit()

    # 3. Customer confirms
    eval_res = await ActionPolicyGate.evaluate_customer_reply(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        message_content=phrase,
    )
    await async_db.commit()
    assert eval_res.decision == ConfirmationDecision.CONFIRMED

    # 4. Execute action
    result = await ActionExecutor.execute_action(
        db=async_db,
        action_id=draft.action_id,
        actor_type=ActionActorType.customer,
        actor_id=customer_id,
    )
    await async_db.commit()

    assert result.success is True
    assert result.status == ActionStatus.completed
    assert result.confirmation_number is not None
    assert f"CNCL-{order_id}" in result.confirmation_number
    assert "successfully canceled" in result.message


@pytest.mark.asyncio
async def test_cancel_order_fails_if_already_shipped(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Precondition rule: order in 'shipped' state fails validation and blocks drafting."""
    action_def = ActionRegistry.get("cancel_order")
    assert action_def is not None

    customer_id = "cust-low-2"
    order_id = "ORD-9999"

    is_valid, msg, _ = await action_def.validate_preconditions(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        parameters={"order_id": order_id, "current_order_status": "shipped"},
    )
    assert is_valid is False
    assert "already shipped" in msg.lower()


@pytest.mark.asyncio
async def test_action_execution_records_audit_journal(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Ensure complete audit journal trail across propose -> confirm -> execute."""
    customer_id = "cust-low-3"
    action_def = ActionRegistry.get("update_communication_preferences")
    assert action_def is not None

    is_valid, _, params = await action_def.validate_preconditions(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        parameters={"email_notifications": True, "sms_notifications": True},
    )
    summary, phrase = action_def.generate_confirmation_phrase(customer_id, params)

    draft, _ = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        action_name=action_def.name,
        risk_level=action_def.risk_level,
        summary_message=summary,
        required_confirmation_phrase=phrase,
        payload=params,
    )
    await async_db.commit()

    # Confirm
    await ActionPolicyGate.evaluate_customer_reply(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        message_content=phrase,
    )
    await async_db.commit()

    # Execute
    await ActionExecutor.execute_action(
        db=async_db,
        action_id=draft.action_id,
        actor_type=ActionActorType.customer,
        actor_id=customer_id,
    )
    await async_db.commit()

    # Query journal
    stmt = (
        select(ActionAuditJournal)
        .where(ActionAuditJournal.action_id == draft.action_id)
        .order_by(ActionAuditJournal.created_at)
    )
    res = await async_db.execute(stmt)
    entries = list(res.scalars().all())

    event_types = [e.event_type for e in entries]
    assert "proposed" in event_types
    assert "confirmed" in event_types
    assert "executing" in event_types
    assert "completed" in event_types

