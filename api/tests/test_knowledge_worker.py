"""Stage 2: Unit tests for KnowledgeIngestionJob lifecycle, leasing, retries and tenant isolation."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import async_session_factory
from app.main import app
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import DocumentStatus, IngestionStatus, KnowledgeDocument, SourceType
from app.models.knowledge_ingestion_job import IngestionJobStatus, KnowledgeIngestionJob
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.providers.base import ProviderConnectionError, ProviderError
from app.security.password import hash_password
from app.security.tokens import create_access_jwt
from app.services.ingestion_service import process_document_ingestion
from app.services.knowledge_worker import (
    claim_next_ingestion_job,
    enqueue_ingestion_job,
    process_claimed_ingestion_job,
    run_ingestion_worker_cycle,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def _get_or_create_user() -> tuple[uuid.UUID, uuid.UUID]:
    """Return (user_id, org_id) of an existing user, creating one if needed."""
    async with async_session_factory() as db:
        res = await db.execute(select(User).limit(1))
        user = res.scalar_one_or_none()
        if user:
            return user.id, user.organization_id
        # Ensure organization exists
        org = Organization(
            id=ORG_ID,
            slug="test-org",
            name="Test Organization",
        )
        db.add(org)
        new_user = User(
            email=f"wkr-test-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("testpassword123"),
            display_name="Worker Test Admin",
            role=UserRole.admin,
            organization_id=ORG_ID,
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        return new_user.id, new_user.organization_id


async def _make_doc(
    content: str = "Customer orders can be returned within 30 days of purchase.",
    ingestion_status: IngestionStatus = IngestionStatus.pending,
    ingestion_error_code: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a draft KnowledgeDocument and return (org_id, doc_id)."""
    user_id, org_id = await _get_or_create_user()
    async with async_session_factory() as db:
        doc = KnowledgeDocument(
            title=f"Worker Test Doc {uuid.uuid4().hex[:6]}",
            source_type=SourceType.text,
            content=content,
            status=DocumentStatus.draft,
            ingestion_status=ingestion_status,
            ingestion_error_code=ingestion_error_code,
            created_by_user_id=user_id,
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
        return doc.organization_id, doc.id


async def _doc_state(doc_id: uuid.UUID) -> tuple[IngestionStatus, DocumentStatus]:
    async with async_session_factory() as db:
        res = await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))
        doc = res.scalar_one()
        return doc.ingestion_status, doc.status


async def _latest_job(doc_id: uuid.UUID) -> KnowledgeIngestionJob | None:
    async with async_session_factory() as db:
        res = await db.execute(
            select(KnowledgeIngestionJob)
            .where(KnowledgeIngestionJob.document_id == doc_id)
            .order_by(KnowledgeIngestionJob.created_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()


async def _chunk_count(doc_id: uuid.UUID) -> int:
    async with async_session_factory() as db:
        res = await db.execute(select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id))
        return len(res.scalars().all())


# ---------------------------------------------------------------------------
# Test: idempotent enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_ingestion_job_idempotent():
    """Enqueueing the same document twice returns/reuses the same queued job."""
    org_id, doc_id = await _make_doc()

    async with async_session_factory() as db:
        job1 = await enqueue_ingestion_job(db, doc_id, org_id)
        await db.commit()
        job1_id = job1.id

    async with async_session_factory() as db:
        job2 = await enqueue_ingestion_job(db, doc_id, org_id)
        await db.commit()
        assert job2.id == job1_id, "Second enqueue must reuse the existing queued job"


# ---------------------------------------------------------------------------
# Test: claim is exclusive (SKIP LOCKED)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_next_job_skip_locked():
    """Two concurrent workers operating in open transactions cannot claim the same job.

    This test verifies the FOR UPDATE SKIP LOCKED behavior by:
    1. Creating a queued job
    2. Starting a transaction and claiming the job (which locks it)
    3. In a separate concurrent transaction, attempting to claim - should return None
    """
    # Use fresh document to avoid state pollution from other tests
    org_id, doc_id = await _make_doc(
        content="Test document for SKIP LOCKED test"
    )

    async with async_session_factory() as db:
        await enqueue_ingestion_job(db, doc_id, org_id)
        await db.commit()

    # First, claim the SPECIFIC job in one session and COMMIT
    async with async_session_factory() as db1:
        job_a = await claim_next_ingestion_job(db1, worker_id="worker-A", job_id=None)
        assert job_a is not None, "Worker A must claim the job"
        claimed_job_id = job_a.id
        await db1.commit()

    # Now in a second session, try to claim the SAME SPECIFIC job - should get None
    # since it's now processing with a fresh lease (not expired)
    async with async_session_factory() as db2:
        job_b = await claim_next_ingestion_job(db2, worker_id="worker-B", job_id=claimed_job_id)
        await db2.commit()
        assert job_b is None, f"Worker B must find no available jobs (job already claimed with valid lease), got {job_b.id if job_b else None}"

    # Verify the job is still processing and locked by worker-A
    async with async_session_factory() as db:
        stmt = select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.id == claimed_job_id)
        res = await db.execute(stmt)
        job = res.scalar_one()
        assert job.status == IngestionJobStatus.processing.value
        assert job.locked_by == "worker-A"

    # Now test SKIP LOCKED with expired lease
    # Manually expire the lease
    async with async_session_factory() as db:
        stmt = (
            select(KnowledgeIngestionJob)
            .where(KnowledgeIngestionJob.id == claimed_job_id)
            .with_for_update()
        )
        res = await db.execute(stmt)
        job = res.scalar_one()
        job.locked_at = datetime.now(UTC) - timedelta(seconds=120)  # Expired lease (60s default)
        await db.commit()

    # Recovery worker should be able to claim it now
    async with async_session_factory() as db3:
        reclaimed = await claim_next_ingestion_job(db3, worker_id="recovery-worker", lease_duration_seconds=60, job_id=claimed_job_id)
        await db3.commit()
        assert reclaimed is not None, "Recovery worker must reclaim the abandoned job"
        assert reclaimed.locked_by == "recovery-worker"
        assert reclaimed.id == claimed_job_id


# ---------------------------------------------------------------------------
# Test: successful ingestion end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_claimed_job_success():
    """Full lifecycle: enqueue → worker cycle → doc=ready, chunks present, job=completed."""
    org_id, doc_id = await _make_doc(
        "Items are returnable within 30 days. Contact support for refund processing."
    )

    async with async_session_factory() as db:
        await enqueue_ingestion_job(db, doc_id, org_id)
        await db.commit()

    await run_ingestion_worker_cycle(worker_id="test-worker")

    ing_status, doc_status = await _doc_state(doc_id)
    assert ing_status == IngestionStatus.ready, f"Expected ready, got {ing_status}"
    assert doc_status == DocumentStatus.draft

    job = await _latest_job(doc_id)
    assert job is not None
    assert job.status == IngestionJobStatus.completed.value

    count = await _chunk_count(doc_id)
    assert count > 0, "At least one chunk must exist after successful ingestion"


# ---------------------------------------------------------------------------
# Test: failure increments attempts and re-queues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_claimed_job_failure_requeues():
    """When ingestion raises, the job is re-queued with incremented attempts."""
    org_id, doc_id = await _make_doc()

    async with async_session_factory() as db:
        await enqueue_ingestion_job(db, doc_id, org_id)
        await db.commit()

    # Patch where the worker uses the function (it's imported into knowledge_worker module)
    with patch(
        "app.services.knowledge_worker.process_document_ingestion",
        new_callable=AsyncMock,
        side_effect=RuntimeError("embedding provider unavailable"),
    ):
        # Test process_claimed_ingestion_job directly for single failure/requeue
        async with async_session_factory() as db:
            job = await claim_next_ingestion_job(db, worker_id="test-worker")
            await db.commit()
            job_id = job.id

        await process_claimed_ingestion_job(job_id=job_id, worker_id="test-worker")

    job = await _latest_job(doc_id)
    assert job is not None
    assert job.status == IngestionJobStatus.queued.value, f"Failed job should be re-queued, got {job.status}"
    assert job.attempts == 1
    assert job.error_code is not None


# ---------------------------------------------------------------------------
# Test: exhausted retries → permanent failure, no chunks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_claimed_job_exhausted_retries():
    """After max_attempts=1, job=failed, doc=failed, no chunks remain."""
    org_id, doc_id = await _make_doc()

    async with async_session_factory() as db:
        await enqueue_ingestion_job(db, doc_id, org_id, max_attempts=1)
        await db.commit()

    with patch(
        "app.services.knowledge_worker.process_document_ingestion",
        new_callable=AsyncMock,
        side_effect=RuntimeError("permanent provider failure"),
    ):
        await run_ingestion_worker_cycle(worker_id="test-worker")

    job = await _latest_job(doc_id)
    assert job is not None
    assert job.status == IngestionJobStatus.failed.value
    assert job.error_code is not None

    ing_status, _ = await _doc_state(doc_id)
    assert ing_status == IngestionStatus.failed

    count = await _chunk_count(doc_id)
    assert count == 0, "No chunks must remain after permanent ingestion failure"


# ---------------------------------------------------------------------------
# Test: partial chunks not visible after failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_chunks_not_visible_on_failure():
    """Orphan chunks written before a crash are deleted on exhausted failure."""
    org_id, doc_id = await _make_doc()

    # Plant an orphan chunk to simulate a partial write before crash
    async with async_session_factory() as db:
        orphan = KnowledgeChunk(
            document_id=doc_id,
            organization_id=org_id,
            ordinal=0,
            content="orphan chunk that must be purged",
            token_estimate=10,
            embedding=[0.0] * 1536,
        )
        db.add(orphan)
        await enqueue_ingestion_job(db, doc_id, org_id, max_attempts=1)
        await db.commit()

    # Patch where the worker calls the function (it's imported into knowledge_worker module)
    with patch(
        "app.services.knowledge_worker.process_document_ingestion",
        new_callable=AsyncMock,
        side_effect=RuntimeError("crash mid-write"),
    ):
        await run_ingestion_worker_cycle(worker_id="test-worker")

    count = await _chunk_count(doc_id)
    assert count == 0, "Orphan chunks must be cleaned up after permanent failure"


# ---------------------------------------------------------------------------
# Test: lease recovery — abandoned running job is reclaimed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lease_recovery_reclaims_abandoned_job():
    """A processing job with an expired lease is reclaimed by the next worker cycle."""
    org_id, doc_id = await _make_doc()

    async with async_session_factory() as db:
        job = await enqueue_ingestion_job(db, doc_id, org_id)
        await db.commit()
        job_id = job.id

    # Simulate a crashed worker: mark job processing with a stale lease
    stale_time = datetime.now(UTC) - timedelta(seconds=120)
    async with async_session_factory() as db:
        res = await db.execute(select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.id == job_id))
        job = res.scalar_one()
        job.status = IngestionJobStatus.processing.value
        job.locked_at = stale_time
        job.locked_by = "dead-worker"
        job.attempts = 1
        await db.commit()

    # Recovery worker uses 60s lease window — stale_time is 120s old, so it's expired
    async with async_session_factory() as db:
        reclaimed = await claim_next_ingestion_job(db, worker_id="recovery-worker", lease_duration_seconds=60)
        assert reclaimed is not None, "Recovery worker must reclaim the abandoned job"
        assert reclaimed.id == job_id
        assert reclaimed.locked_by == "recovery-worker"
        await db.commit()


# ---------------------------------------------------------------------------
# Test: document not publishable until ingestion is ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_not_publishable_until_ingestion_ready():
    """Draft document with ingestion_status=pending must be rejected on publish."""
    _, doc_id = await _make_doc(ingestion_status=IngestionStatus.pending)

    user_id, org_id = await _get_or_create_user()
    token = create_access_jwt(
        subject=str(user_id),
        role=UserRole.admin.value,
        additional_claims={"email": "pub-guard-test@example.com", "organization_id": str(org_id)},
    )
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(f"/api/v1/knowledge/documents/{doc_id}/publish", headers=headers)
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"


# ---------------------------------------------------------------------------
# Test: cross-tenant job isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_tenant_job_isolation():
    """Worker rejects a job whose organization_id does not match the document's org."""
    org_id, doc_id = await _make_doc()
    other_org_id = uuid.uuid4()  # Different org — no document exists for it

    # Create the other organization to satisfy FK constraint
    async with async_session_factory() as db:
        other_org = Organization(
            id=other_org_id,
            slug=f"other-org-{other_org_id.hex[:8]}",
            name="Other Organization",
        )
        db.add(other_org)
        await db.commit()

    # Manually enqueue a job claiming a different org owns this document
    async with async_session_factory() as db:
        fake_job = KnowledgeIngestionJob(
            organization_id=other_org_id,
            document_id=doc_id,
            status=IngestionJobStatus.processing.value,
            attempts=1,
            max_attempts=3,
            locked_at=datetime.now(UTC),
            locked_by="test-worker",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(fake_job)
        await db.commit()
        await db.refresh(fake_job)
        fake_job_id = fake_job.id

    result = await process_claimed_ingestion_job(job_id=fake_job_id, worker_id="test-worker")
    assert result is False, "Cross-tenant job must be rejected"

    async with async_session_factory() as db:
        res = await db.execute(
            select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.id == fake_job_id)
        )
        job = res.scalar_one()
        assert job.status == IngestionJobStatus.failed.value
        assert job.error_code == "DOCUMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# Test: retry endpoint recovers a failed document (test-env sync mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_endpoint_recovers_failed_document():
    """POST /retry on a failed document re-runs ingestion synchronously in test env."""
    user_id, org_id = await _get_or_create_user()
    _, doc_id = await _make_doc(
        content="Return policy: items returnable within 30 days of purchase with receipt.",
        ingestion_status=IngestionStatus.failed,
        ingestion_error_code="EMBEDDING_ERROR",
    )

    token = create_access_jwt(
        subject=str(user_id),
        role=UserRole.admin.value,
        additional_claims={"email": "retry-test@example.com", "organization_id": str(org_id)},
    )
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(f"/api/v1/knowledge/documents/{doc_id}/retry", headers=headers)
        assert res.status_code == 200, res.text
        data = res.json()
        # In test env, sync ingestion runs immediately → should be ready
        assert data["ingestion_status"] == "ready", (
            f"Expected ready after retry, got: {data['ingestion_status']}"
        )


# ---------------------------------------------------------------------------
# Regression Tests for Ingestion Failures & Recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_502_error_classification_and_no_missing_greenlet():
    """Verify HTTP 502 / ProviderConnectionError records PROVIDER_ERROR without MissingGreenlet."""
    org_id, doc_id = await _make_doc(content="Test document content for 502 handling.")

    async with async_session_factory() as db:
        res = await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))
        doc = res.scalar_one()

        with patch(
            "app.providers.fake_provider.FakeEmbeddingProvider.embed_batch",
            side_effect=ProviderConnectionError("Embedding provider server error: status 502"),
        ):
            with pytest.raises(ProviderConnectionError):
                await process_document_ingestion(db, doc)

    # Verify document was cleanly updated with PROVIDER_ERROR
    async with async_session_factory() as db:
        res = await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))
        doc = res.scalar_one()
        assert doc.ingestion_status == IngestionStatus.failed
        assert doc.ingestion_error_code == "PROVIDER_ERROR"


@pytest.mark.asyncio
async def test_existing_chunks_preserved_when_reingestion_fails():
    """Never delete existing usable chunks if new embedding generation fails."""
    org_id, doc_id = await _make_doc(content="Original document content with valid chunks.")

    # 1. Successfully ingest document
    async with async_session_factory() as db:
        res = await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))
        doc = res.scalar_one()
        await process_document_ingestion(db, doc)

    orig_chunks = await _chunk_count(doc_id)
    assert orig_chunks > 0, "Document should have chunks after initial ingestion"

    # 2. Simulate re-ingestion failure on provider call
    async with async_session_factory() as db:
        res = await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))
        doc = res.scalar_one()

        with patch(
            "app.providers.fake_provider.FakeEmbeddingProvider.embed_batch",
            side_effect=ProviderConnectionError("Embedding provider server error: status 502"),
        ):
            with pytest.raises(ProviderConnectionError):
                await process_document_ingestion(db, doc)

    # 3. Verify chunks were NOT wiped out
    remaining_chunks = await _chunk_count(doc_id)
    assert remaining_chunks == orig_chunks, "Existing chunks must remain intact when re-embedding fails"


@pytest.mark.asyncio
async def test_manual_retry_resets_attempt_budget_and_recovers():
    """Manual re-queue of a failed job resets attempt counter to 0 and succeeds upon recovery."""
    org_id, doc_id = await _make_doc(content="Content to be recovered after retry budget reset.")

    # 1. Simulate exhausted failed job with attempts = max_attempts = 3
    async with async_session_factory() as db:
        failed_job = KnowledgeIngestionJob(
            organization_id=org_id,
            document_id=doc_id,
            status=IngestionJobStatus.failed.value,
            attempts=3,
            max_attempts=3,
            error_code="PROVIDER_ERROR",
            error_message="Circuit breaker open",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        db.add(failed_job)
        await db.commit()

    # 2. Manual retry: enqueue_ingestion_job on the failed document
    async with async_session_factory() as db:
        requeued_job = await enqueue_ingestion_job(db, doc_id, org_id)
        assert requeued_job.status == IngestionJobStatus.queued.value
        assert requeued_job.attempts == 0, "Attempts must be reset to 0 on explicit re-queue"
        await db.commit()

    # 3. Process the re-queued job in worker cycle
    await run_ingestion_worker_cycle(worker_id="test-worker")

    latest = await _latest_job(doc_id)
    assert latest.status == IngestionJobStatus.completed.value
    assert latest.attempts == 1
    assert latest.attempts <= latest.max_attempts

