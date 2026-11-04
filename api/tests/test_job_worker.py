"""Tests for Sub-phase 5A.4: Durable Asynchronous Job Execution & Worker Leases."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import async_session_factory
from app.main import app
from app.models.assistant_job import AssistantJob, AssistantJobStatus
from app.models.conversation import Conversation, ConversationStatus
from app.models.handoff import Handoff, HandoffStatus
from app.models.message import Message, SenderType
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.services.assistant_service import HANDOFF_MESSAGE_TEXT
from app.services.job_worker import (
    claim_next_job,
    enqueue_job,
    process_claimed_job,
    run_worker_cycle,
)


@pytest.fixture
async def setup_test_conversation():
    """Create test conversation and visitor session."""
    async with async_session_factory() as db:
        now = datetime.now(UTC)
        visitor = Visitor(
            name="Job Worker Test Visitor",
            email="worker_test@example.com",
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

        token = f"tok_job_{uuid.uuid4()}"
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
        await db.refresh(session)

        return {
            "conversation_id": conv.id,
            "visitor_id": visitor.id,
            "session": session,
            "token": token,
        }


@pytest.mark.asyncio
async def test_visitor_message_enqueues_job_transactionally(setup_test_conversation):
    """Verify visitor message creation enqueues an assistant job transactionally."""
    data = setup_test_conversation
    token = data["token"]
    conv_id = data["conversation_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client_msg_id = str(uuid.uuid4())
        resp = await client.post(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "content": "What is the return policy?",
                "client_message_id": client_msg_id,
            },
        )
        assert resp.status_code == 201
        msg_data = resp.json()
        assert msg_data["content"] == "What is the return policy?"
        message_id = uuid.UUID(msg_data["id"])

    # Query the database to verify the job was enqueued and processed
    async with async_session_factory() as db:
        stmt = select(AssistantJob).where(AssistantJob.message_id == message_id)
        res = await db.execute(stmt)
        job = res.scalar_one_or_none()

        assert job is not None
        assert job.conversation_id == conv_id
        assert job.message_id == message_id
        # Background task runs run_worker_cycle asynchronously, so status is either queued or completed
        assert job.status in (AssistantJobStatus.queued.value, AssistantJobStatus.completed.value)

    # Also directly test transactional enqueueing
    async with async_session_factory() as db:
        test_msg = Message(
            conversation_id=conv_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="Direct enqueue test",
            created_at=datetime.now(UTC),
        )
        db.add(test_msg)
        await db.flush()

        direct_job = await enqueue_job(db, conversation_id=conv_id, message_id=test_msg.id)
        assert direct_job.status == AssistantJobStatus.queued.value
        assert direct_job.attempts == 0
        assert direct_job.locked_at is None
        assert direct_job.locked_by is None
        await db.commit()


@pytest.mark.asyncio
async def test_conversation_scoped_endpoint_enqueues_job(setup_test_conversation):
    """Verify posting to /conversations/{conversation_id}/messages enqueues assistant job asynchronously."""
    data = setup_test_conversation
    token = data["token"]
    conv_id = data["conversation_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client_msg_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/widget/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "content": "Conversation-scoped route job test",
                "client_message_id": client_msg_id,
            },
        )
        assert resp.status_code == 201
        msg_data = resp.json()
        message_id = uuid.UUID(msg_data["id"])

    # Query the database to verify the job was enqueued
    async with async_session_factory() as db:
        stmt = select(AssistantJob).where(AssistantJob.message_id == message_id)
        res = await db.execute(stmt)
        job = res.scalar_one_or_none()

        assert job is not None
        assert job.conversation_id == conv_id
        assert job.message_id == message_id
        assert job.status in (AssistantJobStatus.queued.value, AssistantJobStatus.completed.value)


@pytest.mark.asyncio
async def test_worker_claims_lease_and_completes_job(setup_test_conversation):
    """Verify worker claims job lease, executes pipeline, and marks job completed."""
    data = setup_test_conversation
    conv_id = data["conversation_id"]

    # 1. Enqueue job manually
    async with async_session_factory() as db:
        msg = Message(
            conversation_id=conv_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="Hello there!",
            created_at=datetime.now(UTC),
        )
        db.add(msg)
        await db.flush()

        job = await enqueue_job(db, conversation_id=conv_id, message_id=msg.id)
        await db.commit()
        job_id = job.id

    # 2. Worker claims job
    worker_id = "test_worker_1"
    async with async_session_factory() as db:
        claimed = await claim_next_job(db, worker_id=worker_id, lease_duration_seconds=30, job_id=job_id)
        assert claimed is not None
        assert claimed.id == job_id
        assert claimed.status == AssistantJobStatus.running.value
        assert claimed.locked_by == worker_id
        assert claimed.locked_at is not None
        assert claimed.attempts == 1
        await db.commit()

    # 3. Process claimed job
    success = await process_claimed_job(job_id=job_id, worker_id=worker_id)
    assert success is True

    # 4. Verify job marked completed
    async with async_session_factory() as db:
        stmt = select(AssistantJob).where(AssistantJob.id == job_id)
        res = await db.execute(stmt)
        completed_job = res.scalar_one()

        assert completed_job.status == AssistantJobStatus.completed.value
        assert completed_job.completed_at is not None

        # Verify assistant message was created
        msg_stmt = (
            select(Message)
            .where(
                Message.conversation_id == conv_id,
                Message.sender_type == SenderType.assistant,
            )
        )
        msg_res = await db.execute(msg_stmt)
        asst_msg = msg_res.scalar_one_or_none()
        assert asst_msg is not None


@pytest.mark.asyncio
async def test_abandoned_job_reclaimed_after_lease_expiry(setup_test_conversation):
    """Verify an abandoned job with expired lease is reclaimed by another worker."""
    data = setup_test_conversation
    conv_id = data["conversation_id"]

    now = datetime.now(UTC)
    # Create an abandoned job locked 45s ago (lease is 30s)
    async with async_session_factory() as db:
        msg = Message(
            conversation_id=conv_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="Abandoned message test",
            created_at=now - timedelta(seconds=60),
        )
        db.add(msg)
        await db.flush()

        abandoned_job = AssistantJob(
            conversation_id=conv_id,
            message_id=msg.id,
            status=AssistantJobStatus.running.value,
            attempts=1,
            max_attempts=2,
            locked_at=now - timedelta(seconds=45),
            locked_by="dead_worker_old",
            created_at=now - timedelta(seconds=60),
        )
        db.add(abandoned_job)
        await db.commit()
        job_id = abandoned_job.id

    # New worker claims the job
    new_worker = "healthy_worker_new"
    async with async_session_factory() as db:
        reclaimed = await claim_next_job(db, worker_id=new_worker, lease_duration_seconds=30, job_id=job_id)
        assert reclaimed is not None
        assert reclaimed.id == job_id
        assert reclaimed.status == AssistantJobStatus.running.value
        assert reclaimed.locked_by == new_worker
        assert reclaimed.attempts == 2  # incremented attempts
        await db.commit()


@pytest.mark.asyncio
async def test_staff_takeover_suppresses_pending_job_output(setup_test_conversation):
    """Verify if conversation is claimed by staff, pending job is marked discarded without sending AI message."""
    data = setup_test_conversation
    conv_id = data["conversation_id"]

    # 1. Enqueue job
    async with async_session_factory() as db:
        msg = Message(
            conversation_id=conv_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="I need help please",
            created_at=datetime.now(UTC),
        )
        db.add(msg)
        await db.flush()

        job = await enqueue_job(db, conversation_id=conv_id, message_id=msg.id)
        await db.commit()
        job_id = job.id

    # 2. Worker claims job
    worker_id = "test_worker_takeover"
    async with async_session_factory() as db:
        claimed = await claim_next_job(db, worker_id=worker_id, job_id=job_id)
        assert claimed is not None
        await db.commit()

    # 3. Simulate staff takeover before worker processes
    async with async_session_factory() as db:
        conv_stmt = select(Conversation).where(Conversation.id == conv_id).with_for_update()
        conv_res = await db.execute(conv_stmt)
        conv = conv_res.scalar_one()
        conv.status = ConversationStatus.staff_active
        await db.commit()

    # 4. Worker processes the job
    processed = await process_claimed_job(job_id=job_id, worker_id=worker_id)
    assert processed is True

    # 5. Verify job was discarded and NO assistant message was posted
    async with async_session_factory() as db:
        stmt = select(AssistantJob).where(AssistantJob.id == job_id)
        res = await db.execute(stmt)
        job = res.scalar_one()
        assert job.status == AssistantJobStatus.discarded.value

        # Zero assistant messages
        msg_stmt = (
            select(Message)
            .where(
                Message.conversation_id == conv_id,
                Message.sender_type == SenderType.assistant,
            )
        )
        msg_res = await db.execute(msg_stmt)
        asst_msgs = msg_res.scalars().all()
        assert len(asst_msgs) == 0


@pytest.mark.asyncio
async def test_fatal_job_failure_triggers_safe_handoff(setup_test_conversation):
    """Verify terminal job failure (attempts >= max_attempts) creates safe handoff ticket and message."""
    data = setup_test_conversation
    conv_id = data["conversation_id"]

    # 1. Enqueue job with attempts already at 1 (so next attempt is max_attempts = 2)
    async with async_session_factory() as db:
        msg = Message(
            conversation_id=conv_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="Simulate fatal error",
            created_at=datetime.now(UTC),
        )
        db.add(msg)
        await db.flush()

        job = AssistantJob(
            conversation_id=conv_id,
            message_id=msg.id,
            status=AssistantJobStatus.queued.value,
            attempts=1,  # Already attempted once
            max_attempts=2,
            created_at=datetime.now(UTC),
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    # 2. Worker claims job (attempts becomes 2)
    worker_id = "test_worker_fatal"
    async with async_session_factory() as db:
        claimed = await claim_next_job(db, worker_id=worker_id, job_id=job_id)
        assert claimed is not None
        assert claimed.attempts == 2
        await db.commit()

    # 3. Process job with pipeline raising a fatal exception
    with (
        patch("app.services.job_worker.multi_agent_workflow_enabled", return_value=False),
        patch(
            "app.services.job_worker.handle_assistant_pipeline",
            side_effect=RuntimeError("Unrecoverable downstream provider crash"),
        ),
    ):
        success = await process_claimed_job(job_id=job_id, worker_id=worker_id)
        assert success is False

    # 4. Verify terminal failure state
    async with async_session_factory() as db:
        stmt = select(AssistantJob).where(AssistantJob.id == job_id)
        res = await db.execute(stmt)
        job = res.scalar_one()

        assert job.status == AssistantJobStatus.failed.value
        assert "RuntimeError" in job.error_message

        # Verify safe handoff message was posted
        msg_stmt = (
            select(Message)
            .where(
                Message.conversation_id == conv_id,
                Message.sender_type == SenderType.assistant,
            )
        )
        msg_res = await db.execute(msg_stmt)
        asst_msg = msg_res.scalar_one()
        assert asst_msg.content == HANDOFF_MESSAGE_TEXT

        # Verify handoff ticket was created
        h_stmt = select(Handoff).where(Handoff.conversation_id == conv_id)
        h_res = await db.execute(h_stmt)
        handoff = h_res.scalar_one()
        assert handoff.status == HandoffStatus.queued


@pytest.mark.asyncio
async def test_retryable_job_failure_requeues_for_retry(setup_test_conversation):
    """Verify first failure (attempts < max_attempts) requeues job for retry."""
    data = setup_test_conversation
    conv_id = data["conversation_id"]

    # 1. Enqueue fresh job (attempts = 0)
    async with async_session_factory() as db:
        msg = Message(
            conversation_id=conv_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="Simulate transient error",
            created_at=datetime.now(UTC),
        )
        db.add(msg)
        await db.flush()

        job = await enqueue_job(db, conversation_id=conv_id, message_id=msg.id)
        await db.commit()
        job_id = job.id

    # 2. Worker claims job (attempts becomes 1)
    worker_id = "test_worker_transient"
    async with async_session_factory() as db:
        claimed = await claim_next_job(db, worker_id=worker_id, job_id=job_id)
        assert claimed is not None
        await db.commit()

    # 3. Process job with temporary failure
    with (
        patch("app.services.job_worker.multi_agent_workflow_enabled", return_value=False),
        patch(
            "app.services.job_worker.handle_assistant_pipeline",
            side_effect=TimeoutError("Transient network timeout"),
        ),
    ):
        success = await process_claimed_job(job_id=job_id, worker_id=worker_id)
        assert success is False

    # 4. Verify job is requeued (status == queued, lock cleared)
    async with async_session_factory() as db:
        stmt = select(AssistantJob).where(AssistantJob.id == job_id)
        res = await db.execute(stmt)
        job = res.scalar_one()

        assert job.status == AssistantJobStatus.queued.value
        assert job.locked_at is None
        assert job.locked_by is None
        assert job.attempts == 1
        assert "TimeoutError" in job.error_message
