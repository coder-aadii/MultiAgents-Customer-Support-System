from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.actions.base import ActionDefinition, ActionResult, RiskLevel
from app.actions.executor import ActionExecutor
from app.actions.idempotency import IdempotencyEngine
from app.actions.policy import ActionPolicyGate, ConfirmationDecision
from app.actions.registry import ActionRegistry
from app.config import settings
from app.models.action_audit_journal import ActionActorType
from app.models.business_action import ActionStatus, BusinessAction
from app.models.conversation import Conversation, ConversationStatus
from app.models.visitor import Visitor


class FlakyTimeoutAction(ActionDefinition):
    name = "flaky_timeout_action"
    risk_level = RiskLevel.LOW
    description = "Simulates an upstream API that times out once before succeeding"

    def __init__(self):
        self.call_count = 0

    async def validate_preconditions(self, db, conversation_id, customer_id, parameters):
        return True, "Valid flaky parameters", parameters

    def generate_confirmation_phrase(self, customer_id, validated_parameters):
        return "Execute flaky action", "CONFIRM FLAKY"

    async def execute(self, db, action: BusinessAction) -> ActionResult:
        self.call_count += 1
        if self.call_count == 1:
            raise TimeoutError("Upstream payment gateway gateway timeout (504)")
        return ActionResult(
            action_id=action.id,
            status=ActionStatus.completed,
            success=True,
            confirmation_number=f"RETRY-OK-{action.idempotency_key[:8]}",
            message="Succeeded after upstream recovery",
        )


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def sample_conversation(async_db: AsyncSession) -> Conversation:
    visitor = Visitor(name="Chaos Tester", email="chaos@example.com")
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
async def test_external_api_timeout_retries_safely_with_idempotency_key(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """Test that after an upstream timeout, retrying with the same idempotency key recovers safely."""
    flaky = FlakyTimeoutAction()
    ActionRegistry.register(flaky)

    customer_id = "cust-chaos-1"
    draft, _ = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        action_name=flaky.name,
        risk_level=flaky.risk_level,
        summary_message="Execute flaky action",
        required_confirmation_phrase="CONFIRM FLAKY",
        payload={"param": "value1"},
    )
    await async_db.commit()

    # Confirm
    await ActionPolicyGate.evaluate_customer_reply(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=customer_id,
        message_content="CONFIRM FLAKY",
    )
    await async_db.commit()

    # First execution attempt triggers timeout
    res1 = await ActionExecutor.execute_action(
        db=async_db,
        action_id=draft.action_id,
        actor_type=ActionActorType.customer,
        actor_id=customer_id,
    )
    await async_db.commit()

    assert res1.success is False
    assert res1.status == ActionStatus.failed
    assert "timeout" in res1.message.lower()

    # Second execution attempt (retry) recovers safely
    res2 = await ActionExecutor.execute_action(
        db=async_db,
        action_id=draft.action_id,
        actor_type=ActionActorType.customer,
        actor_id=customer_id,
    )
    await async_db.commit()

    assert res2.success is True
    assert res2.status == ActionStatus.completed
    assert "succeeded" in res2.message.lower()

    # Third execution returns cached result without re-executing
    res3 = await ActionExecutor.execute_action(
        db=async_db,
        action_id=draft.action_id,
        actor_type=ActionActorType.customer,
        actor_id=customer_id,
    )
    assert res3.success is True
    assert res3.confirmation_number == res2.confirmation_number
    # Flaky action execute was not called a 3rd time
    assert flaky.call_count == 2


@pytest.mark.asyncio
async def test_cross_customer_action_tampering_is_blocked(
    async_db: AsyncSession, sample_conversation: Conversation
):
    """BOLA Defense: Customer B cannot confirm or access an action drafted for Customer A."""
    victim_customer_id = "cust-victim-99"
    attacker_customer_id = "cust-attacker-66"
    order_id = "ORD-PRIVATE-11"

    action_def = ActionRegistry.get("cancel_order")
    assert action_def is not None

    _, _, params = await action_def.validate_preconditions(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=victim_customer_id,
        parameters={"order_id": order_id, "current_order_status": "processing"},
    )
    summary, phrase = action_def.generate_confirmation_phrase(victim_customer_id, params)

    draft, _ = await IdempotencyEngine.create_or_get_draft(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=victim_customer_id,
        action_name=action_def.name,
        risk_level=action_def.risk_level,
        summary_message=summary,
        required_confirmation_phrase=phrase,
        payload=params,
    )
    await async_db.commit()

    # Attacker tries to confirm the victim's draft
    attacker_eval = await ActionPolicyGate.evaluate_customer_reply(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=attacker_customer_id,
        message_content=phrase,
    )
    await async_db.commit()

    # Must be denied (no active action visible to attacker)
    assert attacker_eval.decision == ConfirmationDecision.NO_ACTIVE_ACTION

    # Verify action is still awaiting confirmation for the true owner
    victim_eval = await ActionPolicyGate.evaluate_customer_reply(
        db=async_db,
        conversation_id=sample_conversation.id,
        customer_id=victim_customer_id,
        message_content=phrase,
    )
    await async_db.commit()
    assert victim_eval.decision == ConfirmationDecision.CONFIRMED

