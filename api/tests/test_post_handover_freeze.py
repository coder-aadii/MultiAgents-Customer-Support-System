"""Tests verifying the Post-Handover Zero-LLM Invariant (Phase 1, Step 1).

Invariants:
1. Once a conversation is handed over (waiting_for_staff, staff_active, or has a queued/claimed handoff ticket),
   is_ai_handling_allowed returns False.
2. Visitor messages posted to a handed-over conversation do NOT enqueue assistant jobs.
3. If an assistant job was previously queued, the worker discards it immediately without any LLM calls.
4. Triage and generation pipelines immediately short-circuit with None if conversation is handed over.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import async_session_factory
from app.main import app
from app.models.assistant_job import AssistantJob, AssistantJobStatus
from app.models.conversation import Conversation, ConversationStatus
from app.models.handoff import Handoff, HandoffReason, HandoffStatus
from app.models.message import Message, SenderType
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.services.assistant_service import is_ai_handling_allowed, handle_assistant_pipeline
from app.services.job_worker import enqueue_job, process_claimed_job
from app.services.multi_agent_service import handle_multi_agent_pipeline
from app.services.triage_service import classify_message


@pytest.fixture
async def setup_freeze_test_env():
    """Create test conversation, visitor, and session for post-handover freeze tests."""
    async with async_session_factory() as db:
        now = datetime.now(UTC)
        visitor = Visitor(
            name="Freeze Test Visitor",
            email="freeze_test@example.com",
            created_at=now,
            last_seen_at=now,
        )
        db.add(visitor)
        await db.flush()

        conv = Conversation(
            visitor_id=visitor.id,
            status=ConversationStatus.open,
            created_at=now,
            updated_at=now,
        )
        db.add(conv)
        await db.flush()

        token = f"tok_freeze_{uuid.uuid4()}"
        session = VisitorSession(
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            origin="http://localhost:5174",
            expires_at=now + timedelta(hours=24),
            created_at=now,
        )
        db.add(session)
        await db.commit()
        await db.refresh(conv)
        await db.refresh(visitor)
        await db.refresh(session)
        return {"conv_id": conv.id, "visitor_id": visitor.id, "token": token, "session_id": session.id}


@pytest.mark.asyncio
async def test_is_ai_handling_allowed_matrix(setup_freeze_test_env):
    """Verify is_ai_handling_allowed accurately reflects conversation eligibility."""
    conv_id = setup_freeze_test_env["conv_id"]
    async with async_session_factory() as db:
        # 1. Open conversation without handoff -> Allowed
        allowed = await is_ai_handling_allowed(db, conv_id)
        assert allowed is True

        # 2. Conversation in waiting_for_staff -> Disallowed
        conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one()
        conv.status = ConversationStatus.waiting_for_staff
        await db.commit()
        allowed = await is_ai_handling_allowed(db, conv_id)
        assert allowed is False

        # 3. Conversation in staff_active -> Disallowed
        conv.status = ConversationStatus.staff_active
        await db.commit()
        allowed = await is_ai_handling_allowed(db, conv_id)
        assert allowed is False

        # 4. Conversation closed -> Disallowed
        conv.status = ConversationStatus.closed
        await db.commit()
        allowed = await is_ai_handling_allowed(db, conv_id)
        assert allowed is False

        # 5. Conversation open but with queued handoff ticket -> Disallowed
        conv.status = ConversationStatus.open
        handoff = Handoff(
            conversation_id=conv_id,
            reason=HandoffReason.unsupported_request,
            summary="Test queued handoff",
            status=HandoffStatus.queued,
        )
        db.add(handoff)
        await db.commit()
        allowed = await is_ai_handling_allowed(db, conv_id)
        assert allowed is False

        # 6. Handoff resolved and conversation open -> Allowed
        handoff.status = HandoffStatus.resolved
        await db.commit()
        allowed = await is_ai_handling_allowed(db, conv_id)
        assert allowed is True


@pytest.mark.asyncio
async def test_worker_discards_job_when_waiting_for_staff(setup_freeze_test_env):
    """If a conversation transitioned to waiting_for_staff, worker must discard job with 0 LLM calls."""
    conv_id = setup_freeze_test_env["conv_id"]

    async with async_session_factory() as db:
        now = datetime.now(UTC)
        msg = Message(
            conversation_id=conv_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="Can someone help me?",
            created_at=now,
        )
        db.add(msg)
        await db.flush()

        job = await enqueue_job(db, conversation_id=conv_id, message_id=msg.id)

        # Transition conversation to waiting_for_staff before worker processes job
        conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one()
        conv.status = ConversationStatus.waiting_for_staff
        await db.commit()

        job_id = job.id

    with patch("app.services.job_worker.handle_assistant_pipeline", new_callable=AsyncMock) as mock_legacy, \
         patch("app.services.job_worker.handle_multi_agent_pipeline", new_callable=AsyncMock) as mock_multi:
        processed = await process_claimed_job(
            db_factory=async_session_factory,
            worker_id="test_freeze_worker",
            job_id=job_id,
        )
        assert processed is True
        mock_legacy.assert_not_called()
        mock_multi.assert_not_called()

    async with async_session_factory() as db:
        j = (await db.execute(select(AssistantJob).where(AssistantJob.id == job_id))).scalar_one()
        assert j.status == AssistantJobStatus.discarded.value


@pytest.mark.asyncio
async def test_worker_discards_job_when_active_handoff_exists(setup_freeze_test_env):
    """If conversation has an active handoff ticket, worker must discard job with 0 LLM calls."""
    conv_id = setup_freeze_test_env["conv_id"]

    async with async_session_factory() as db:
        now = datetime.now(UTC)
        msg = Message(
            conversation_id=conv_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="Follow up question",
            created_at=now,
        )
        db.add(msg)
        await db.flush()

        job = await enqueue_job(db, conversation_id=conv_id, message_id=msg.id)

        handoff = Handoff(
            conversation_id=conv_id,
            reason=HandoffReason.policy,
            summary="Queued handoff ticket",
            status=HandoffStatus.queued,
        )
        db.add(handoff)
        await db.commit()

        job_id = job.id

    with patch("app.services.job_worker.handle_assistant_pipeline", new_callable=AsyncMock) as mock_legacy, \
         patch("app.services.job_worker.handle_multi_agent_pipeline", new_callable=AsyncMock) as mock_multi:
        processed = await process_claimed_job(
            db_factory=async_session_factory,
            worker_id="test_freeze_worker",
            job_id=job_id,
        )
        assert processed is True
        mock_legacy.assert_not_called()
        mock_multi.assert_not_called()

    async with async_session_factory() as db:
        j = (await db.execute(select(AssistantJob).where(AssistantJob.id == job_id))).scalar_one()
        assert j.status == AssistantJobStatus.discarded.value


@pytest.mark.asyncio
async def test_triage_and_generation_pipelines_block_when_handed_over(setup_freeze_test_env):
    """Direct calls to classify_message, handle_assistant_pipeline, and handle_multi_agent_pipeline return None."""
    conv_id = setup_freeze_test_env["conv_id"]

    async with async_session_factory() as db:
        conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one()
        conv.status = ConversationStatus.waiting_for_staff
        await db.commit()

        with patch("app.providers.fake_provider.FakeLLMProvider.chat", new_callable=AsyncMock) as mock_llm:
            triage_res = await classify_message(
                db=db,
                conversation_id=conv_id,
                current_message="Is anyone there?",
            )
            assert triage_res is None
            mock_llm.assert_not_called()

            legacy_res = await handle_assistant_pipeline(
                db=db,
                conversation_id=conv_id,
                visitor_message_content="Is anyone there?",
            )
            assert legacy_res is None
            mock_llm.assert_not_called()

            multi_res = await handle_multi_agent_pipeline(
                db=db,
                conversation_id=conv_id,
                visitor_message_content="Is anyone there?",
            )
            assert multi_res is None
            mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_widget_post_message_does_not_enqueue_job_after_handover(setup_freeze_test_env):
    """POST /api/v1/widget/messages does not enqueue an assistant job if conversation is waiting_for_staff."""
    conv_id = setup_freeze_test_env["conv_id"]
    token = setup_freeze_test_env["token"]

    async with async_session_factory() as db:
        conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one()
        conv.status = ConversationStatus.waiting_for_staff
        await db.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:5174"},
    ) as client:
        resp = await client.post(
            "/api/v1/widget/messages",
            json={
                "content": "Message after handoff to staff",
                "client_message_id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 200

    async with async_session_factory() as db:
        # Verify message was recorded
        msg_stmt = select(Message).where(Message.conversation_id == conv_id, Message.content == "Message after handoff to staff")
        msg = (await db.execute(msg_stmt)).scalar_one_or_none()
        assert msg is not None

        # Verify NO assistant job was enqueued for this message
        job_stmt = select(AssistantJob).where(AssistantJob.message_id == msg.id)
        job = (await db.execute(job_stmt)).scalar_one_or_none()
        assert job is None


@pytest.mark.asyncio
async def test_is_ai_handling_allowed_claimed_and_multiple_handoffs(setup_freeze_test_env):
    """Adversarial check: claimed handoffs and multiple historical handoffs."""
    conv_id = setup_freeze_test_env["conv_id"]
    async with async_session_factory() as db:
        # 1. Claimed handoff -> AI handling must be disallowed
        h_claimed = Handoff(
            conversation_id=conv_id,
            reason=HandoffReason.unsupported_request,
            summary="Claimed by staff agent",
            status=HandoffStatus.claimed,
        )
        db.add(h_claimed)
        await db.commit()
        assert await is_ai_handling_allowed(db, conv_id) is False

        # 2. Add multiple historical resolved handoffs while claimed handoff exists -> Still disallowed
        for i in range(3):
            h_resolved = Handoff(
                conversation_id=conv_id,
                reason=HandoffReason.unsupported_request,
                summary=f"Past resolved ticket {i}",
                status=HandoffStatus.resolved,
            )
            db.add(h_resolved)
        await db.commit()
        assert await is_ai_handling_allowed(db, conv_id) is False

        # 3. Resolve the active claimed handoff (now only resolved tickets remain) -> Allowed
        h_claimed.status = HandoffStatus.resolved
        await db.commit()
        assert await is_ai_handling_allowed(db, conv_id) is True

        # 4. Non-existent conversation UUID -> Returns False gracefully (no unhandled exception)
        assert await is_ai_handling_allowed(db, uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_worker_discards_job_when_handoff_is_claimed(setup_freeze_test_env):
    """If conversation handoff is in claimed status, worker must discard with 0 LLM calls."""
    conv_id = setup_freeze_test_env["conv_id"]

    async with async_session_factory() as db:
        now = datetime.now(UTC)
        msg = Message(
            conversation_id=conv_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="Help while claimed",
            created_at=now,
        )
        db.add(msg)
        await db.flush()

        job = await enqueue_job(db, conversation_id=conv_id, message_id=msg.id)

        handoff = Handoff(
            conversation_id=conv_id,
            reason=HandoffReason.policy,
            summary="Claimed by staff agent",
            status=HandoffStatus.claimed,
        )
        db.add(handoff)
        await db.commit()
        job_id = job.id

    with patch("app.services.job_worker.handle_assistant_pipeline", new_callable=AsyncMock) as mock_legacy, \
         patch("app.services.job_worker.handle_multi_agent_pipeline", new_callable=AsyncMock) as mock_multi:
        processed = await process_claimed_job(
            db_factory=async_session_factory,
            worker_id="test_freeze_worker_claimed",
            job_id=job_id,
        )
        assert processed is True
        mock_legacy.assert_not_called()
        mock_multi.assert_not_called()

    async with async_session_factory() as db:
        j = (await db.execute(select(AssistantJob).where(AssistantJob.id == job_id))).scalar_one()
        assert j.status == AssistantJobStatus.discarded.value


@pytest.mark.asyncio
async def test_job_worker_terminal_failure_suppresses_duplicate_handoff_when_handed_over(setup_freeze_test_env):
    """When a job hits terminal retry failure on a handed-over conversation,

    it must NOT post duplicate handoff messages or overwrite existing handoffs.
    """
    conv_id = setup_freeze_test_env["conv_id"]

    async with async_session_factory() as db:
        now = datetime.now(UTC)
        conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one()
        conv.status = ConversationStatus.waiting_for_staff
        await db.commit()

        msg = Message(
            conversation_id=conv_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="Trigger terminal failure",
            created_at=now,
        )
        db.add(msg)
        await db.flush()

        job = await enqueue_job(db, conversation_id=conv_id, message_id=msg.id)
        # Set attempts to max to trigger terminal failure path
        job.attempts = job.max_attempts
        await db.commit()
        job_id = job.id

        # Count baseline messages
        baseline_msgs = (
            await db.execute(select(Message).where(Message.conversation_id == conv_id))
        ).scalars().all()
        baseline_count = len(baseline_msgs)

    # Force an execution failure
    with patch("app.services.job_worker.handle_assistant_pipeline", side_effect=RuntimeError("Terminal LLM Crash")):
        await process_claimed_job(
            db_factory=async_session_factory,
            worker_id="test_terminal_worker",
            job_id=job_id,
        )

    async with async_session_factory() as db:
        after_msgs = (
            await db.execute(select(Message).where(Message.conversation_id == conv_id))
        ).scalars().all()
        # No extra handoff message should have been added because conv.status != open
        assert len(after_msgs) == baseline_count


@pytest.mark.asyncio
async def test_fuzz_is_ai_handling_allowed_inputs():
    """Fuzz test is_ai_handling_allowed with varied UUID inputs and edge states."""
    async with async_session_factory() as db:
        # 1. Random nil / non-existent UUIDs
        for _ in range(20):
            random_id = uuid.uuid4()
            allowed = await is_ai_handling_allowed(db, random_id)
            assert allowed is False

        # 2. Nil UUID
        nil_id = uuid.UUID(int=0)
        assert await is_ai_handling_allowed(db, nil_id) is False


@pytest.mark.asyncio
async def test_persist_response_aborts_if_handoff_created_during_generation(setup_freeze_test_env):
    """Verify _persist_response drops AI response if a handoff was created while LLM was running."""
    from app.services.multi_agent_service import _persist_response
    conv_id = setup_freeze_test_env["conv_id"]

    async with async_session_factory() as db:
        # Create an active handoff ticket while conversation is still open
        h = Handoff(
            conversation_id=conv_id,
            reason=HandoffReason.policy,
            summary="Staff handoff created during LLM run",
            status=HandoffStatus.queued,
        )
        db.add(h)
        await db.commit()

        msg = await _persist_response(
            db=db,
            conversation_id=conv_id,
            tenant_id=uuid.uuid4(),
            content="Late AI Response",
            resolution=SimpleNamespace(citations=()),
        )
        assert msg is None

        # Verify no message was persisted
        persisted = (
            await db.execute(select(Message).where(Message.conversation_id == conv_id, Message.content == "Late AI Response"))
        ).scalar_one_or_none()
        assert persisted is None

