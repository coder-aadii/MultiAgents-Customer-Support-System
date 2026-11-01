"""Tests for Phase 4 Sub-phase 4.2: Triage agent classification, schemas, and guards."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.database import async_session_factory
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, SenderType
from app.models.visitor import Visitor
from app.providers import FakeLLMProvider, LLMResponse
from app.schemas.assistant import TriageAction, TriageResult
from app.services.triage_service import classify_message


@pytest.fixture
async def setup_conversation():
    """Setup a test visitor and open conversation."""
    async with async_session_factory() as session:
        vis = Visitor(id=uuid.uuid4(), name="Test Visitor", email="test_vis@example.com")
        session.add(vis)
        await session.flush()

        conv = Conversation(
            id=uuid.uuid4(),
            visitor_id=vis.id,
            status=ConversationStatus.open,
        )
        session.add(conv)
        await session.commit()
        return {"conversation_id": conv.id, "visitor_id": vis.id}


@pytest.mark.asyncio
async def test_triage_action_answer(setup_conversation):
    """Ensure standard FAQ queries classify as 'answer'."""
    conv_id = setup_conversation["conversation_id"]
    fake_provider = FakeLLMProvider()

    async with async_session_factory() as session:
        result = await classify_message(
            db=session,
            conversation_id=conv_id,
            current_message="Where can I find the user manual?",
            llm_provider=fake_provider,
        )
        await session.commit()

        assert result is not None
        assert result.action == TriageAction.answer
        assert result.reason == "faq_match"

        # Verify audit event
        audit_res = await session.execute(
            select(AuditEvent).where(
                AuditEvent.resource_id == conv_id,
                AuditEvent.event_type == "assistant.triage",
            )
        )
        audit = audit_res.scalar_one_or_none()
        assert audit is not None
        assert audit.metadata_json["action"] == "answer"
        assert audit.metadata_json["reason"] == "faq_match"
        assert "provider_metadata" in audit.metadata_json
        assert "prompt" not in audit.metadata_json


@pytest.mark.asyncio
async def test_triage_action_handoff_human_request(setup_conversation):
    """Ensure human agent requests classify as 'handoff'."""
    conv_id = setup_conversation["conversation_id"]
    fake_provider = FakeLLMProvider()

    async with async_session_factory() as session:
        result = await classify_message(
            db=session,
            conversation_id=conv_id,
            current_message="I need to talk to a human agent right now",
            llm_provider=fake_provider,
        )
        await session.commit()

        assert result is not None
        assert result.action == TriageAction.handoff
        assert result.reason == "human_request"


@pytest.mark.asyncio
async def test_triage_action_handoff_refund_and_billing(setup_conversation):
    """Ensure refund, payment, and account requests classify as 'handoff'."""
    conv_id = setup_conversation["conversation_id"]
    fake_provider = FakeLLMProvider()

    async with async_session_factory() as session:
        result = await classify_message(
            db=session,
            conversation_id=conv_id,
            current_message="Can you process a refund for my last invoice?",
            llm_provider=fake_provider,
        )
        await session.commit()

        assert result is not None
        assert result.action == TriageAction.handoff
        assert result.reason == "account_or_billing_request"


@pytest.mark.asyncio
async def test_triage_action_handoff_unsafe_domain(setup_conversation):
    """Ensure medical, legal, and emergency requests classify as 'handoff'."""
    conv_id = setup_conversation["conversation_id"]
    fake_provider = FakeLLMProvider()

    async with async_session_factory() as session:
        result = await classify_message(
            db=session,
            conversation_id=conv_id,
            current_message="I have an emergency medical issue",
            llm_provider=fake_provider,
        )
        await session.commit()

        assert result is not None
        assert result.action == TriageAction.handoff
        assert result.reason == "unsafe_domain"


@pytest.mark.asyncio
async def test_triage_action_clarify(setup_conversation):
    """Ensure unclear requests classify as 'clarify'."""
    conv_id = setup_conversation["conversation_id"]
    fake_provider = FakeLLMProvider()

    async with async_session_factory() as session:
        result = await classify_message(
            db=session,
            conversation_id=conv_id,
            current_message="Please clarify the missing detail",
            llm_provider=fake_provider,
        )
        await session.commit()

        assert result is not None
        assert result.action == TriageAction.clarify
        assert result.reason == "missing_detail"


@pytest.mark.asyncio
async def test_triage_malformed_json_fallback_handoff(setup_conversation):
    """Ensure unparseable LLM output safely falls back to handoff."""
    conv_id = setup_conversation["conversation_id"]
    fake_provider = FakeLLMProvider()
    fake_provider.queue_response("Invalid raw text without json {")

    async with async_session_factory() as session:
        result = await classify_message(
            db=session,
            conversation_id=conv_id,
            current_message="Hello",
            llm_provider=fake_provider,
        )
        await session.commit()

        assert result is not None
        assert result.action == TriageAction.handoff
        assert result.reason == "provider_or_parse_failure"


@pytest.mark.asyncio
async def test_triage_unknown_action_fallback_handoff(setup_conversation):
    """Ensure unauthorized/unknown LLM action safely falls back to handoff."""
    conv_id = setup_conversation["conversation_id"]
    fake_provider = FakeLLMProvider()
    fake_provider.queue_response(json.dumps({"action": "execute_shell", "reason": "exploit"}))

    async with async_session_factory() as session:
        result = await classify_message(
            db=session,
            conversation_id=conv_id,
            current_message="Hello",
            llm_provider=fake_provider,
        )
        await session.commit()

        assert result is not None
        assert result.action == TriageAction.handoff
        assert result.reason == "unknown_action_fallback"


@pytest.mark.asyncio
async def test_triage_no_provider_after_staff_active(setup_conversation):
    """Invariant: NEVER invoke provider if conversation is staff_active."""
    conv_id = setup_conversation["conversation_id"]
    fake_provider = FakeLLMProvider()

    async with async_session_factory() as session:
        conv = await session.get(Conversation, conv_id)
        assert conv is not None
        conv.status = ConversationStatus.staff_active
        await session.commit()

    async with async_session_factory() as session:
        result = await classify_message(
            db=session,
            conversation_id=conv_id,
            current_message="Hello",
            llm_provider=fake_provider,
        )

        assert result is None
        assert fake_provider.call_count == 0


@pytest.mark.asyncio
async def test_triage_no_provider_when_closed(setup_conversation):
    """Invariant: NEVER invoke provider if conversation is closed."""
    conv_id = setup_conversation["conversation_id"]
    fake_provider = FakeLLMProvider()

    async with async_session_factory() as session:
        conv = await session.get(Conversation, conv_id)
        assert conv is not None
        conv.status = ConversationStatus.closed
        await session.commit()

    async with async_session_factory() as session:
        result = await classify_message(
            db=session,
            conversation_id=conv_id,
            current_message="Hello",
            llm_provider=fake_provider,
        )

        assert result is None
        assert fake_provider.call_count == 0


@pytest.mark.asyncio
async def test_triage_excludes_internal_staff_notes(setup_conversation):
    """Ensure staff notes/messages are strictly excluded from triage prompt."""
    conv_id = setup_conversation["conversation_id"]
    fake_provider = FakeLLMProvider()

    async with async_session_factory() as session:
        # Add visitor message
        session.add(
            Message(
                conversation_id=conv_id,
                sender_type=SenderType.visitor,
                content="Visitor visible question",
            )
        )
        # Add staff internal note
        session.add(
            Message(
                conversation_id=conv_id,
                sender_type=SenderType.staff,
                content="INTERNAL_SECRET: Customer is requesting a high-value discount",
            )
        )
        # Add assistant message
        session.add(
            Message(
                conversation_id=conv_id,
                sender_type=SenderType.assistant,
                content="Assistant previous answer",
            )
        )
        await session.commit()

    async with async_session_factory() as session:
        await classify_message(
            db=session,
            conversation_id=conv_id,
            current_message="Follow up question",
            llm_provider=fake_provider,
        )

        assert fake_provider.call_count == 1
        call_req = fake_provider.calls[0]
        full_content = " ".join([m.content for m in call_req.messages])

        # Staff message must NOT be in the LLM prompt
        assert "INTERNAL_SECRET" not in full_content
        assert "Visitor visible question" in full_content
        assert "Assistant previous answer" in full_content
