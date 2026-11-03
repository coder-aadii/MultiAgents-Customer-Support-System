from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import (
    DocumentStatus,
    IngestionStatus,
    KnowledgeDocument,
)
from app.models.knowledge_ingestion_job import (
    IngestionJobStatus,
    KnowledgeIngestionJob,
)
from app.services.ingestion_service import (
    IngestionError,
    process_document_ingestion,
)

logger = logging.getLogger("mcss.services.knowledge_worker")

DEFAULT_LEASE_DURATION_SECONDS = 60
MAX_INGESTION_RETRIES = 3


async def enqueue_ingestion_job(
    db: AsyncSession,
    document_id: uuid.UUID,
    organization_id: uuid.UUID,
    max_attempts: int = MAX_INGESTION_RETRIES,
) -> KnowledgeIngestionJob:
    """Idempotently enqueues or resets a durable background ingestion job for a document."""
    # Check for existing active job for this document
    stmt = (
        select(KnowledgeIngestionJob)
        .where(
            KnowledgeIngestionJob.document_id == document_id,
            KnowledgeIngestionJob.organization_id == organization_id,
        )
        .order_by(KnowledgeIngestionJob.created_at.desc())
        .limit(1)
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing and existing.status in (
        IngestionJobStatus.queued.value,
        IngestionJobStatus.processing.value,
    ):
        return existing

    if existing and existing.status == IngestionJobStatus.failed.value:
        # Re-queue existing failed job for retry
        existing.status = IngestionJobStatus.queued.value
        existing.locked_at = None
        existing.locked_by = None
        existing.error_code = None
        existing.error_message = None
        existing.completed_at = None
        await db.flush()
        return existing

    job = KnowledgeIngestionJob(
        organization_id=organization_id,
        document_id=document_id,
        status=IngestionJobStatus.queued.value,
        attempts=0,
        max_attempts=max_attempts,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(job)
    await db.flush()
    return job


async def claim_next_ingestion_job(
    db: AsyncSession,
    worker_id: str,
    lease_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
    job_id: uuid.UUID | None = None,
) -> KnowledgeIngestionJob | None:
    """Claims the next available ingestion job using SELECT ... FOR UPDATE SKIP LOCKED.

    Reclaims abandoned running jobs whose lease (locked_at) has expired.
    If job_id is specified, claims that specific job if eligible.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=lease_duration_seconds)

    conditions = [
        or_(
            KnowledgeIngestionJob.status == IngestionJobStatus.queued.value,
            and_(
                KnowledgeIngestionJob.status == IngestionJobStatus.processing.value,
                KnowledgeIngestionJob.locked_at < cutoff,
            ),
        )
    ]
    if job_id is not None:
        conditions.append(KnowledgeIngestionJob.id == job_id)

    stmt = (
        select(KnowledgeIngestionJob)
        .where(*conditions)
        .order_by(KnowledgeIngestionJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if not job:
        return None

    job.status = IngestionJobStatus.processing.value
    job.locked_at = now
    job.locked_by = worker_id
    job.attempts += 1
    job.updated_at = now
    await db.flush()
    return job


async def process_claimed_ingestion_job(
    job_id: uuid.UUID,
    worker_id: str,
    db_factory=async_session_factory,
) -> bool:
    """Executes the ingestion pipeline for a claimed background ingestion job.

    Enforces:
    - Verifies document exists and is in draft state.
    - Updates document status to processing.
    - Executes extraction, overlapping chunking, and embedding generation.
    - Atomically replaces knowledge chunks without partial embeddings left visible.
    - Handles failure and retries up to max_attempts; sets failed state when exhausted.
    """
    doc_id: uuid.UUID | None = None
    org_id: uuid.UUID | None = None
    attempts: int = 0
    max_attempts: int = MAX_INGESTION_RETRIES

    # Step 1: Pre-execution validation & document binding
    async with db_factory() as db:
        stmt = (
            select(KnowledgeIngestionJob)
            .where(KnowledgeIngestionJob.id == job_id)
            .with_for_update()
        )
        res = await db.execute(stmt)
        job = res.scalar_one_or_none()
        if not job or job.status != IngestionJobStatus.processing.value:
            return False

        doc_id = job.document_id
        org_id = job.organization_id
        attempts = job.attempts
        max_attempts = job.max_attempts

        # Fetch document
        doc_stmt = (
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.id == doc_id,
                KnowledgeDocument.organization_id == org_id,
            )
            .with_for_update()
        )
        doc_res = await db.execute(doc_stmt)
        doc = doc_res.scalar_one_or_none()

        if not doc:
            # Document was deleted or does not belong to tenant
            job.status = IngestionJobStatus.failed.value
            job.error_code = "DOCUMENT_NOT_FOUND"
            job.error_message = "Target knowledge document was not found or was deleted."
            job.completed_at = datetime.now(UTC)
            job.updated_at = datetime.now(UTC)
            await db.commit()
            return False

        # Only draft documents may be processed
        if doc.status != DocumentStatus.draft:
            job.status = IngestionJobStatus.completed.value
            job.error_code = None
            job.error_message = f"Document is already in '{doc.status.value}' state; skipping."
            job.completed_at = datetime.now(UTC)
            job.updated_at = datetime.now(UTC)
            await db.commit()
            return True

        # Mark document as processing
        doc.ingestion_status = IngestionStatus.processing
        doc.ingestion_error_code = None
        await db.commit()

    # Step 2: Ingestion Execution (Chunking + Embedding)
    ingestion_success = False
    error_code = "INGESTION_FAILED"
    error_message: str | None = None

    async with db_factory() as db:
        try:
            doc_stmt = (
                select(KnowledgeDocument)
                .where(KnowledgeDocument.id == doc_id)
            )
            doc_res = await db.execute(doc_stmt)
            doc = doc_res.scalar_one_or_none()

            if doc:
                await process_document_ingestion(db, doc)
                ingestion_success = (doc.ingestion_status == IngestionStatus.ready)
                if not ingestion_success:
                    error_code = doc.ingestion_error_code or "INGESTION_FAILED"
                    error_message = "Ingestion pipeline reported failure"
        except Exception as exc:
            logger.exception("Ingestion failed for document %s: %s", doc_id, exc)
            if isinstance(exc, IngestionError):
                error_code = exc.code
                error_message = exc.message
            elif isinstance(exc, asyncio.TimeoutError | TimeoutError):
                error_code = "TIMEOUT"
                error_message = "Ingestion operation timed out"
            else:
                error_code = "INTERNAL_ERROR"
                error_message = str(exc)[:255]

    # Step 3: Transactional Outcome Finalization
    async with db_factory() as db:
        stmt = (
            select(KnowledgeIngestionJob)
            .where(KnowledgeIngestionJob.id == job_id)
            .with_for_update()
        )
        res = await db.execute(stmt)
        job = res.scalar_one_or_none()
        if not job:
            return False

        doc_stmt = (
            select(KnowledgeDocument)
            .where(KnowledgeDocument.id == doc_id)
            .with_for_update()
        )
        doc_res = await db.execute(doc_stmt)
        doc = doc_res.scalar_one_or_none()

        now = datetime.now(UTC)
        if ingestion_success:
            job.status = IngestionJobStatus.completed.value
            job.completed_at = now
            job.error_code = None
            job.error_message = None
            job.locked_at = None
            job.locked_by = None
            job.updated_at = now
            if doc:
                doc.ingestion_status = IngestionStatus.ready
                doc.ingestion_error_code = None
            await db.commit()
            return True

        # Failure handling: check if retries remain
        if attempts < max_attempts:
            # Re-queue for next worker attempt with backoff
            job.status = IngestionJobStatus.queued.value
            job.locked_at = None
            job.locked_by = None
            job.error_code = error_code
            job.error_message = error_message
            job.updated_at = now
            if doc:
                doc.ingestion_status = IngestionStatus.pending
            logger.warning(
                "Ingestion job %s failed (attempt %d/%d). Re-queued for retry.",
                job.id, attempts, max_attempts
            )
        else:
            # Exhausted retries: mark fatal failure
            job.status = IngestionJobStatus.failed.value
            job.completed_at = now
            job.error_code = error_code
            job.error_message = error_message
            job.locked_at = None
            job.locked_by = None
            job.updated_at = now
            if doc:
                doc.ingestion_status = IngestionStatus.failed
                doc.ingestion_error_code = error_code

            # Guarantee no partial chunks remain
            await db.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id)
            )
            logger.error(
                "Ingestion job %s permanently failed after %d attempts: %s",
                job.id, attempts, error_message
            )

        await db.commit()
        return False


async def run_ingestion_worker_cycle(
    worker_id: str = "knowledge_worker",
    lease_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
    db_factory=async_session_factory,
    job_id: uuid.UUID | None = None,
) -> bool:
    """Drains eligible queued and abandoned knowledge ingestion jobs."""
    handled_any = False
    target_job_id = job_id

    while True:
        claimed_id: uuid.UUID | None = None
        async with db_factory() as db:
            job = await claim_next_ingestion_job(
                db=db,
                worker_id=worker_id,
                lease_duration_seconds=lease_duration_seconds,
                job_id=target_job_id,
            )
            if job:
                claimed_id = job.id
                await db.commit()

        # Clear target_job_id after first claim so remaining queue can be drained
        target_job_id = None

        if not claimed_id:
            break

        await process_claimed_ingestion_job(
            job_id=claimed_id,
            worker_id=worker_id,
            db_factory=db_factory,
        )
        handled_any = True

    return handled_any

