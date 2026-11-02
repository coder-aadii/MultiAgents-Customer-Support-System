from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.actions.base import RiskLevel
from app.actions.executor import ActionExecutor
from app.actions.idempotency import IdempotencyEngine
from app.actions.policy import ActionPolicyGate
from app.actions.reconciliation import ReconciliationService
from app.actions.registry import ActionRegistry
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
    visitor = Visitor(name="Reconcile Tester", email="reconcile@example.com")
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
async def test_reconciliation_verifies_external_state(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Test reconciliation worker confirms synchronized upstream state and writes audit journal."""
    customer_id = "cust-rec-1"
    order_id = "ORD-SYNC-1"
    action_def = ActionRegistry.get("cancel_order")

    _, _, params = await action_def.validate_preconditions(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        parameters={"order_id": order_id, "current_order_status": "processing"},
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

    # Customer confirms & executes
    await ActionPolicyGate.evaluate_customer_reply(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        message_content=phrase,
    )
    await async_db.commit()

    await ActionExecutor.execute_action(
        db=async_db,
        action_id=draft.action_id,
        actor_type=ActionActorType.customer,
        actor_id=customer_id,
    )
    await async_db.commit()

    # Mock external verifier confirming canceled state upstream
    async def mock_external_verifier(action: BusinessAction) -> tuple[bool, dict]:
        return True, {"external_order_id": order_id, "upstream_status": "canceled"}

    synced, msg, details = await ReconciliationService.reconcile_action(
        db=async_db,
        action_id=draft.action_id,
        upstream_status_verifier=mock_external_verifier,
    )
    await async_db.commit()

    assert synced is True
    assert "synchronized" in msg

    # Verify audit journal has reconciled event
    stmt = (
        select(ActionAuditJournal)
        .where(
            ActionAuditJournal.action_id == draft.action_id,
            ActionAuditJournal.event_type == ActionAuditEventType.reconciled.value,
        )
    )
    res = await async_db.execute(stmt)
    entry = res.scalar_one_or_none()
    assert entry is not None
    assert entry.details_json.get("status") == "synchronized"


@pytest.mark.asyncio
async def test_reconciliation_detects_state_mismatch_and_alerts(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Test reconciliation worker detects desynchronization and flags an alert in audit journal."""
    customer_id = "cust-rec-2"
    order_id = "ORD-DESYNC-2"
    action_def = ActionRegistry.get("cancel_order")

    _, _, params = await action_def.validate_preconditions(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        parameters={"order_id": order_id, "current_order_status": "processing"},
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

    await ActionPolicyGate.evaluate_customer_reply(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        message_content=phrase,
    )
    await async_db.commit()

    await ActionExecutor.execute_action(
        db=async_db,
        action_id=draft.action_id,
        actor_type=ActionActorType.customer,
        actor_id=customer_id,
    )
    await async_db.commit()

    # Mock external verifier reporting order is STILL active upstream (mismatch!)
    async def mock_mismatch_verifier(action: BusinessAction) -> tuple[bool, dict]:
        return False, {"external_order_id": order_id, "upstream_status": "shipped_in_transit"}

    synced, msg, details = await ReconciliationService.reconcile_action(
        db=async_db,
        action_id=draft.action_id,
        upstream_status_verifier=mock_mismatch_verifier,
    )
    await async_db.commit()

    assert synced is False
    assert "mismatch" in msg.lower()

    # Verify audit journal has desync alert
    stmt = (
        select(ActionAuditJournal)
        .where(
            ActionAuditJournal.action_id == draft.action_id,
            ActionAuditJournal.event_type == ActionAuditEventType.reconciled.value,
        )
    )
    res = await async_db.execute(stmt)
    entry = res.scalar_one_or_none()
    assert entry is not None
    assert entry.details_json.get("alert") == "DESYNC_ALERT"

