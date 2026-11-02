from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.actions.base import RiskLevel
from app.actions.idempotency import IdempotencyEngine
from app.actions.registry import ActionRegistry
from app.config import settings
from app.models.business_action import ActionStatus, BusinessAction
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, SenderType
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.services.assistant_service import handle_assistant_pipeline


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def authenticated_conversation_and_session(async_db: AsyncSession):
    visitor = Visitor(name="Pipeline Action Tester", email="pipeline@example.com")
    async_db.add(visitor)
    await async_db.flush()

    conv = Conversation(
        visitor_id=visitor.id,
        status=ConversationStatus.open,
    )
    async_db.add(conv)
    await async_db.flush()

    customer_id = f"cust-pipe-{uuid.uuid4().hex[:6]}"
    now = datetime.now(UTC)
    session = VisitorSession(
        visitor_id=visitor.id,
        conversation_id=conv.id,
        token_hash=f"hash-{uuid.uuid4().hex[:16]}",
        origin="http://localhost:5174",
        verified_customer_id=customer_id,
        identity_assurance_level=2,
        customer_token_expires_at=now + timedelta(hours=1),
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    async_db.add(session)
    await async_db.commit()
    return conv, session, customer_id


@pytest.mark.asyncio
async def test_assistant_pipeline_intercepts_confirmation_and_executes(
    async_db: AsyncSession, authenticated_conversation_and_session
):
    """Test that handle_assistant_pipeline detects exact confirmation phrase and executes action directly."""
    conv, session, customer_id = authenticated_conversation_and_session
    order_id = "ORD-PIPE-1"
    action_def = ActionRegistry.get("cancel_order")
    assert action_def is not None

    _, _, params = await action_def.validate_preconditions(
        db=async_db,
        conversation_id=conv.id,
        customer_id=customer_id,
        parameters={"order_id": order_id, "current_order_status": "processing"},
    )
    summary, phrase = action_def.generate_confirmation_phrase(customer_id, params)

    draft, _ = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=conv.id,
        customer_id=customer_id,
        action_name=action_def.name,
        risk_level=action_def.risk_level,
        summary_message=summary,
        required_confirmation_phrase=phrase,
        payload=params,
    )
    await async_db.commit()

    # Visitor sends exact confirmation phrase into assistant pipeline
    reply_msg = await handle_assistant_pipeline(
        db=async_db,
        conversation_id=conv.id,
        visitor_message_content=phrase,
    )

    assert reply_msg is not None
    assert reply_msg.sender_type == SenderType.assistant
    assert "successfully canceled" in reply_msg.content
    assert reply_msg.metadata_json.get("decision") == "confirmed"

    # Action in DB is completed
    action_res = await async_db.get(BusinessAction, draft.action_id)
    assert action_res is not None
    assert action_res.status == ActionStatus.completed.value


@pytest.mark.asyncio
async def test_assistant_pipeline_handles_ambiguous_reply_with_prompt(
    async_db: AsyncSession, authenticated_conversation_and_session
):
    """Test that sending 'yes cancel it' does not execute and returns clarification guidance."""
    conv, session, customer_id = authenticated_conversation_and_session
    order_id = "ORD-PIPE-2"
    action_def = ActionRegistry.get("cancel_order")

    _, _, params = await action_def.validate_preconditions(
        db=async_db,
        conversation_id=conv.id,
        customer_id=customer_id,
        parameters={"order_id": order_id, "current_order_status": "processing"},
    )
    summary, phrase = action_def.generate_confirmation_phrase(customer_id, params)

    draft, _ = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=conv.id,
        customer_id=customer_id,
        action_name=action_def.name,
        risk_level=action_def.risk_level,
        summary_message=summary,
        required_confirmation_phrase=phrase,
        payload=params,
    )
    await async_db.commit()

    # Visitor sends ambiguous message
    reply_msg = await handle_assistant_pipeline(
        db=async_db,
        conversation_id=conv.id,
        visitor_message_content="yes please cancel it right away",
    )

    assert reply_msg is not None
    assert "exact phrase" in reply_msg.content
    assert phrase in reply_msg.content

    # Action is still awaiting confirmation
    action_res = await async_db.get(BusinessAction, draft.action_id)
    assert action_res is not None
    assert action_res.status == ActionStatus.awaiting_confirmation.value


@pytest.mark.asyncio
async def test_assistant_pipeline_drafts_action_from_customer_intent(
    async_db: AsyncSession, authenticated_conversation_and_session
):
    """Test that customer intent 'Please cancel order ORD-7777' drafts the action dynamically."""
    conv, session, customer_id = authenticated_conversation_and_session
    order_id = "ORD-7777"

    # Visitor sends intent message without pre-existing draft
    reply_msg = await handle_assistant_pipeline(
        db=async_db,
        conversation_id=conv.id,
        visitor_message_content=f"I want to cancel order {order_id} please",
    )

    assert reply_msg is not None
    assert reply_msg.sender_type == SenderType.assistant
    assert f"Canceling order {order_id} cannot be undone" in reply_msg.content
    assert f"CONFIRM CANCEL {order_id}" in reply_msg.content
    assert reply_msg.metadata_json.get("action") == "action_draft_created"

    # Verify action was drafted in database
    action_id = uuid.UUID(reply_msg.metadata_json["action_id"])
    draft_action = await async_db.get(BusinessAction, action_id)
    assert draft_action is not None
    assert draft_action.action_name == "cancel_order"
    assert draft_action.status == ActionStatus.awaiting_confirmation.value
    assert draft_action.payload_json["order_id"] == order_id

    # Now customer replies with exact confirmation
    confirm_msg = await handle_assistant_pipeline(
        db=async_db,
        conversation_id=conv.id,
        visitor_message_content=f"CONFIRM CANCEL {order_id}",
    )
    assert confirm_msg is not None
    assert f"Order {order_id} has been successfully canceled" in confirm_msg.content
    assert confirm_msg.metadata_json.get("decision") == "confirmed"

    # Action is completed
    await async_db.refresh(draft_action)
    assert draft_action.status == ActionStatus.completed.value


@pytest.mark.asyncio
async def test_assistant_pipeline_unauthenticated_cancellation_intent(
    async_db: AsyncSession
):
    """Unauthenticated visitor sending cancellation intent is instructed to authenticate."""
    visitor = Visitor(name="Unauth Visitor", email="unauth@example.com")
    async_db.add(visitor)
    await async_db.flush()

    conv = Conversation(
        visitor_id=visitor.id,
        status=ConversationStatus.open,
    )
    async_db.add(conv)
    await async_db.commit()

    reply_msg = await handle_assistant_pipeline(
        db=async_db,
        conversation_id=conv.id,
        visitor_message_content="cancel order ORD-1234",
    )

    assert reply_msg is not None
    assert "log in to your store account" in reply_msg.content
    assert reply_msg.metadata_json.get("reason") == "authentication_required"
