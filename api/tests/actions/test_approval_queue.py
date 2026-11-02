from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.actions.approval_service import ApprovalService
from app.actions.base import RiskLevel
from app.actions.idempotency import IdempotencyEngine
from app.actions.policy import ActionPolicyGate, ConfirmationDecision
from app.actions.registry import ActionRegistry
from app.config import settings
from app.models.business_action import ActionStatus, BusinessAction
from app.models.conversation import Conversation, ConversationStatus
from app.models.user import User, UserRole
from app.models.visitor import Visitor
from app.security.password import hash_password


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def sample_conversation(async_db: AsyncSession) -> Conversation:
    visitor = Visitor(name="Approval Tester", email="approval@example.com")
    async_db.add(visitor)
    await async_db.flush()

    conv = Conversation(
        visitor_id=visitor.id,
        status=ConversationStatus.open,
    )
    async_db.add(conv)
    await async_db.commit()
    return conv


@pytest_asyncio.fixture
async def sample_supervisor(async_db: AsyncSession) -> User:
    supervisor = User(
        email=f"supervisor-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Supervisor User",
        role=UserRole.staff,
        is_active=True,
    )
    async_db.add(supervisor)
    await async_db.commit()
    return supervisor


@pytest.mark.asyncio
async def test_high_risk_action_enqueues_for_supervisor_review(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Confirming a HIGH risk action does NOT execute immediately; transitions to awaiting_approval."""
    customer_id = "cust-high-1"
    action_def = ActionRegistry.get("issue_store_credit")
    assert action_def is not None

    _, _, params = await action_def.validate_preconditions(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        parameters={"amount": 45.00, "reason": "Late delivery apology"},
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

    # Customer confirms exact phrase
    eval_res = await ActionPolicyGate.evaluate_customer_reply(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        message_content=phrase,
    )
    await async_db.commit()

    assert eval_res.decision == ConfirmationDecision.CONFIRMED
    assert eval_res.action.status == ActionStatus.awaiting_approval.value
    assert "supervisor review" in eval_res.response_message.lower()

    # Check pending list
    pending = await ApprovalService.list_pending_actions(
        db=async_db, risk_level=RiskLevel.HIGH.value
    )
    assert any(a.id == draft.action_id for a in pending)


@pytest.mark.asyncio
async def test_supervisor_approval_triggers_execution(
    async_db: AsyncSession, sample_conversation: Conversation, sample_supervisor: User
):
    """Supervisor approval unlocks and successfully executes the high-risk action."""
    customer_id = "cust-high-2"
    action_def = ActionRegistry.get("issue_store_credit")

    _, _, params = await action_def.validate_preconditions(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        parameters={"amount": 25.00, "reason": "Product defect"},
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

    # Customer confirms
    await ActionPolicyGate.evaluate_customer_reply(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        message_content=phrase,
    )
    await async_db.commit()

    # Supervisor approves
    result = await ApprovalService.approve_action(
        db=async_db,
        action_id=draft.action_id,
        supervisor_user_id=sample_supervisor.id,
        notes="Approved per store policy #88",
    )
    await async_db.commit()

    assert result.success is True
    assert result.status == ActionStatus.completed
    assert result.confirmation_number is not None
    assert "successfully issued" in result.message


@pytest.mark.asyncio
async def test_supervisor_rejection_aborts_action_and_notifies(
    async_db: AsyncSession, sample_conversation: Conversation, sample_supervisor: User
):
    """Supervisor rejection transitions action to rejected with audit reason."""
    customer_id = "cust-high-3"
    action_def = ActionRegistry.get("issue_store_credit")

    _, _, params = await action_def.validate_preconditions(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        parameters={"amount": 50.00, "reason": "Dissatisfied"},
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

    # Supervisor rejects
    rejected = await ApprovalService.reject_action(
        db=async_db,
        action_id=draft.action_id,
        supervisor_user_id=sample_supervisor.id,
        reason="Claim window of 14 days expired.",
    )
    await async_db.commit()

    assert rejected.status == ActionStatus.rejected.value
    assert rejected.review_notes == "Claim window of 14 days expired."
    assert rejected.reviewed_by_user_id == sample_supervisor.id

