from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.actions.base import RiskLevel
from app.actions.idempotency import IdempotencyEngine
from app.actions.policy import (
    ActionPolicyGate,
    ConfirmationDecision,
    normalize_phrase,
)
from app.config import settings
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
    visitor = Visitor(name="Confirmation Tester", email="confirm@example.com")
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
async def test_action_draft_generates_strict_confirmation_phrase(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Test that action drafts generate an unambiguous required confirmation phrase."""
    customer_id = "cust-conf-1"
    order_id = "ORD-4491"
    required_phrase = f"CONFIRM CANCEL {order_id}"

    draft, _ = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        action_name="cancel_order",
        risk_level=RiskLevel.MEDIUM,
        summary_message=f"Cancel order {order_id}",
        required_confirmation_phrase=required_phrase,
        payload={"order_id": order_id},
    )
    await async_db.commit()

    assert draft.required_confirmation_phrase == "CONFIRM CANCEL ORD-4491"
    assert normalize_phrase("confirm cancel ord-4491!") == "CONFIRM CANCEL ORD-4491"


@pytest.mark.asyncio
async def test_confirmation_succeeds_with_exact_phrase(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Test that providing the exact confirmation phrase transitions action to executing or awaiting_approval."""
    customer_id = "cust-conf-2"
    order_id = "ORD-7722"

    draft, _ = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        action_name="cancel_order",
        risk_level=RiskLevel.MEDIUM,
        summary_message=f"Cancel order {order_id}",
        required_confirmation_phrase=f"CONFIRM CANCEL {order_id}",
        payload={"order_id": order_id},
    )
    await async_db.commit()

    # Exact confirmation reply (case-insensitive)
    eval_result = await ActionPolicyGate.evaluate_customer_reply(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        message_content=f"  confirm cancel {order_id}  ",
    )
    await async_db.commit()

    assert eval_result.decision == ConfirmationDecision.CONFIRMED
    assert eval_result.action.status == ActionStatus.executing.value
    assert eval_result.action.confirmed_at is not None


@pytest.mark.asyncio
async def test_confirmation_fails_on_fuzzy_or_ambiguous_reply(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Test that ambiguous confirmations like 'yes', 'confirm', or partial strings do NOT execute."""
    customer_id = "cust-conf-3"
    order_id = "ORD-8811"

    await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        action_name="cancel_order",
        risk_level=RiskLevel.MEDIUM,
        summary_message=f"Cancel order {order_id}",
        required_confirmation_phrase=f"CONFIRM CANCEL {order_id}",
        payload={"order_id": order_id},
    )
    await async_db.commit()

    ambiguous_inputs = ["yes please", "sure go ahead", "confirm", "ok cancel it", "yes"]

    for msg in ambiguous_inputs:
        eval_result = await ActionPolicyGate.evaluate_customer_reply(
            db=async_db,
            conversation_id=sample_conversation.id,
            customer_id=customer_id,
            message_content=msg,
        )
        await async_db.commit()

        assert eval_result.decision == ConfirmationDecision.AMBIGUOUS
        assert eval_result.action.status == ActionStatus.awaiting_confirmation.value
        assert "CONFIRM CANCEL ORD-8811" in eval_result.response_message


@pytest.mark.asyncio
async def test_action_draft_expires_after_configured_timeout(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Test that after expiration, confirmation attempts fail closed and mark action expired."""
    customer_id = "cust-conf-4"
    order_id = "ORD-9900"

    draft, _ = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        action_name="cancel_order",
        risk_level=RiskLevel.MEDIUM,
        summary_message=f"Cancel order {order_id}",
        required_confirmation_phrase=f"CONFIRM CANCEL {order_id}",
        payload={"order_id": order_id},
    )
    await async_db.commit()

    # Manually expire the draft
    stmt = (
        BusinessAction.__table__.update()
        .where(BusinessAction.id == draft.action_id)
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await async_db.execute(stmt)
    await async_db.commit()

    # Attempt to confirm expired draft
    eval_result = await ActionPolicyGate.evaluate_customer_reply(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        message_content=f"CONFIRM CANCEL {order_id}",
    )
    await async_db.commit()

    assert eval_result.decision == ConfirmationDecision.EXPIRED
    assert eval_result.action.status == ActionStatus.expired.value
    assert "expired" in eval_result.response_message.lower()

