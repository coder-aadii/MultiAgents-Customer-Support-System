from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models.assistant_job import AssistantJob, AssistantJobStatus
from app.models.conversation import Conversation, ConversationStatus
from app.models.handoff import Handoff, HandoffReason, HandoffStatus
from app.models.message import Message, SenderType
from app.services.assistant_service import (
    HANDOFF_MESSAGE_TEXT,
    create_or_reuse_handoff,
    handle_assistant_pipeline,
)
from app.services.multi_agent_service import handle_multi_agent_pipeline

if TYPE_CHECKING:
    pass

logger = logging.getLogger("mcss.services.job_worker")


def multi_agent_workflow_enabled() -> bool:
    """Master runtime gate: both orchestration and durable workflow must be enabled."""
    return bool(
        settings.MCSS_MULTI_AGENT_ORCHESTRATION_ENABLED
        and settings.MCSS_MULTI_AGENT_WORKFLOW_ENABLED
    )


async def enqueue_job(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
) -> AssistantJob:
    """Idempotently enqueues an assistant job for a visitor message."""
    # Check if job already exists for this message
    existing_stmt = select(AssistantJob).where(AssistantJob.message_id == message_id)
    res = await db.execute(existing_stmt)
    existing = res.scalar_one_or_none()
    if existing:
        return existing

    job = AssistantJob(
        conversation_id=conversation_id,
        message_id=message_id,
        status=AssistantJobStatus.queued.value,
        attempts=0,
        max_attempts=2,
        created_at=datetime.now(UTC),
    )
    db.add(job)
    await db.flush()
    return job


async def claim_next_job(
    db: AsyncSession,
    worker_id: str,
    lease_duration_seconds: int = 30,
    job_id: uuid.UUID | None = None,
) -> AssistantJob | None:
    """Claims the next available job using SELECT ... FOR UPDATE SKIP LOCKED.

    Reclaims abandoned running jobs whose lease (locked_at) has expired.
    If job_id is specified, claims that specific job if eligible.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=lease_duration_seconds)

    conditions = [
        or_(
            AssistantJob.status == AssistantJobStatus.queued.value,
            and_(
                AssistantJob.status == AssistantJobStatus.running.value,
                AssistantJob.locked_at < cutoff,
            ),
        )
    ]
    if job_id is not None:
        conditions.append(AssistantJob.id == job_id)

    stmt = (
        select(AssistantJob)
        .where(*conditions)
        .order_by(AssistantJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if not job:
        return None

    job.status = AssistantJobStatus.running.value
    job.locked_at = now
    job.locked_by = worker_id
    job.attempts += 1
    await db.flush()
    return job


async def process_claimed_job(
    job_id: uuid.UUID,
    worker_id: str,
    db_factory=async_session_factory,
) -> bool:
    """Executes the complete processing lifecycle of a claimed assistant job.

    Enforces:
    - Pre-execution check: discards job if conversation transitioned to staff_active or closed.
    - Releases database locks during pipeline / remote connector execution.
    - Transactional re-check after generation: suppresses AI output if staff took over in-flight.
    - Retries transient errors up to max_attempts; triggers safe handoff on fatal failure.
    """
    conversation_id: uuid.UUID
    visitor_content: str

    # Step 1: Pre-execution validation & acquire payload under transaction
    async with db_factory() as db:
        stmt = select(AssistantJob).where(AssistantJob.id == job_id).with_for_update()
        res = await db.execute(stmt)
        job = res.scalar_one_or_none()
        if not job or job.status != AssistantJobStatus.running.value or job.locked_by != worker_id:
            return False

        # Verify conversation status
        conv_stmt = (
            select(Conversation)
            .where(Conversation.id == job.conversation_id)
            .with_for_update()
        )
        conv_res = await db.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()

        if not conv or conv.status != ConversationStatus.open:
            logger.info(
                "Conversation %s is not open for AI (%s); discarding job %s",
                job.conversation_id,
                conv.status.value if conv else "not_found",
                job.id,
            )
            job.status = AssistantJobStatus.discarded.value
            job.completed_at = datetime.now(UTC)
            await db.commit()
            return True

        # Check if conversation has an active handoff ticket
        h_stmt = (
            select(Handoff.id)
            .where(
                Handoff.conversation_id == job.conversation_id,
                Handoff.status.in_([HandoffStatus.queued, HandoffStatus.claimed]),
            )
            .limit(1)
        )
        h_res = await db.execute(h_stmt)
        if h_res.scalar_one_or_none() is not None:
            logger.info(
                "Conversation %s has pending/claimed handoff ticket; discarding job %s",
                job.conversation_id,
                job.id,
            )
            job.status = AssistantJobStatus.discarded.value
            job.completed_at = datetime.now(UTC)
            await db.commit()
            return True

        # Fetch the visitor message content
        msg_stmt = select(Message).where(Message.id == job.message_id)
        msg_res = await db.execute(msg_stmt)
        msg = msg_res.scalar_one_or_none()

        if not msg:
            job.status = AssistantJobStatus.discarded.value
            job.completed_at = datetime.now(UTC)
            await db.commit()
            return True

        conversation_id = job.conversation_id
        visitor_content = msg.content
        await db.commit()

    # Step 2: Generation Execution (Row locks are released during LLM / connector calls)
    execution_error: Exception | None = None
    result_message: Message | None = None

    try:
        async with db_factory() as db:
            if multi_agent_workflow_enabled():
                result_message = await handle_multi_agent_pipeline(
                    db=db,
                    job_id=job_id,
                    conversation_id=conversation_id,
                    visitor_message_content=visitor_content,
                )
            else:
                result_message = await handle_assistant_pipeline(
                    db=db,
                    conversation_id=conversation_id,
                    visitor_message_content=visitor_content,
                )
    except Exception as exc:
        logger.warning(
            "Assistant pipeline failed for job %s: %s",
            job_id,
            exc,
            exc_info=True,
        )
        execution_error = exc

    # Step 3: Transactional Outcome Gate
    async with db_factory() as db:
        stmt = select(AssistantJob).where(AssistantJob.id == job_id).with_for_update()
        res = await db.execute(stmt)
        job = res.scalar_one_or_none()
        if not job:
            return False

        now = datetime.now(UTC)

        if execution_error is None:
            if result_message is None:
                # In-flight takeover detected during generation pipeline
                job.status = AssistantJobStatus.discarded.value
            else:
                job.status = AssistantJobStatus.completed.value
                # Dispatch outbound channel reply if non-widget channel
                try:
                    from app.channels.registry import default_channel_registry
                    await default_channel_registry.dispatch_outbound(db, conversation_id, result_message)
                except Exception as out_err:
                    logger.warning("Failed outbound dispatch for job %s: %s", job_id, out_err)
            job.completed_at = now
            await db.commit()
            return True

        # Error handling & retry / handoff policy
        if job.attempts < job.max_attempts:
            # Retryable error: requeue with cleared lock
            job.status = AssistantJobStatus.queued.value
            job.locked_at = None
            job.locked_by = None
            job.error_message = f"{type(execution_error).__name__}: {execution_error}"
            await db.commit()
            logger.info(
                "Requeued job %s for retry (attempt %d/%d)",
                job.id,
                job.attempts,
                job.max_attempts,
            )
            return False

        # Terminal failure: attempts >= max_attempts
        job.status = AssistantJobStatus.failed.value
        job.completed_at = now
        job.error_message = (
            f"Terminal failure ({job.attempts} attempts): {type(execution_error).__name__}: {execution_error}"
        )

        # Atomically trigger safe staff handoff ticket and message
        try:
            conv_stmt = (
                select(Conversation)
                .where(Conversation.id == conversation_id)
                .with_for_update()
            )
            conv_res = await db.execute(conv_stmt)
            active_conv = conv_res.scalar_one_or_none()

            if active_conv and active_conv.status == ConversationStatus.open:
                # Invariant: Only create and deliver terminal handoff message if conversation does not already have an active handoff ticket
                h_check = (
                    select(Handoff.id)
                    .where(
                        Handoff.conversation_id == conversation_id,
                        Handoff.status.in_([HandoffStatus.queued, HandoffStatus.claimed]),
                    )
                    .limit(1)
                )
                existing_h = (await db.execute(h_check)).scalar_one_or_none()
                if existing_h is None:
                    await create_or_reuse_handoff(
                        db=db,
                        conversation_id=conversation_id,
                        reason=HandoffReason.unsupported_request,
                        summary=f"Worker terminal failure: {type(execution_error).__name__}",
                    )
                    active_conv.status = ConversationStatus.waiting_for_staff
                    active_conv.last_message_at = now
                    active_conv.updated_at = now

                    handoff_msg = Message(
                        conversation_id=conversation_id,
                        client_message_id=uuid.uuid4(),
                        sender_type=SenderType.assistant,
                        content=HANDOFF_MESSAGE_TEXT,
                        metadata_json={"action": "handoff", "reason": "worker_fatal_failure"},
                        created_at=now,
                    )
                    db.add(handoff_msg)
        except Exception as handoff_exc:
            logger.error("Failed to commit terminal handoff fallback: %s", handoff_exc)

        await db.commit()
        return False


async def run_worker_cycle(
    worker_id: str,
    lease_duration_seconds: int = 30,
    db_factory=async_session_factory,
    job_id: uuid.UUID | None = None,
) -> bool:
    """Claims and processes jobs until the queue is empty (or a specific job_id first)."""
    handled_any = False
    target_job_id = job_id

    while True:
        claimed_id: uuid.UUID | None = None
        async with db_factory() as db:
            job = await claim_next_job(
                db=db,
                worker_id=worker_id,
                lease_duration_seconds=lease_duration_seconds,
                job_id=target_job_id,
            )
            if job:
                claimed_id = job.id
                await db.commit()

        # Clear target_job_id after the first claim attempt so the loop drains remaining jobs
        target_job_id = None

        if not claimed_id:
            break

        await process_claimed_job(job_id=claimed_id, worker_id=worker_id, db_factory=db_factory)
        handled_any = True

    return handled_any
