from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.cursor import decode_cursor, encode_cursor
from app.common.errors import AppException, NotFoundError
from app.models.audit_event import ActorType, AuditEvent
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import (
    DocumentStatus,
    IngestionStatus,
    KnowledgeDocument,
    SourceType,
)
from app.schemas.common import PaginatedResponse
from app.schemas.knowledge import ChunkResponse, DocumentDetailResponse, DocumentResponse
from app.services.ingestion_service import process_document_ingestion
from app.services.knowledge_worker import enqueue_ingestion_job
from app.tenancy.filter import TenantQueryScoper


async def create_document(
    db: AsyncSession,
    title: str,
    source_type: SourceType,
    content: str,
    user_id: uuid.UUID,
    source_filename: str | None = None,
    raw_bytes: bytes | None = None,
    enqueue_background: bool = False,
) -> KnowledgeDocument:
    """Create a new draft knowledge document; enqueues background ingestion when enqueue_background=True."""
    doc = KnowledgeDocument(
        title=title,
        source_type=source_type,
        source_filename=source_filename,
        content=content,
        status=DocumentStatus.draft,
        ingestion_status=IngestionStatus.pending,
        created_by_user_id=user_id,
    )
    db.add(doc)
    await db.flush()

    # Log audit event for creation
    audit = AuditEvent(
        actor_type=ActorType.user,
        actor_id=user_id,
        event_type="document_created",
        resource_type="knowledge_document",
        resource_id=doc.id,
        metadata_json={
            "title": title,
            "source_type": source_type.value,
            "source_filename": source_filename,
        },
    )
    db.add(audit)

    if enqueue_background:
        await enqueue_ingestion_job(
            db=db,
            document_id=doc.id,
            organization_id=doc.organization_id,
        )
        await db.commit()
        await db.refresh(doc)
        return doc

    await db.commit()

    # Fallback / sync ingestion for tests/tools
    await process_document_ingestion(db, doc, raw_bytes=raw_bytes)
    await db.refresh(doc)
    return doc


async def list_documents(
    db: AsyncSession,
    status: DocumentStatus | None = None,
    ingestion_status: IngestionStatus | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> PaginatedResponse[DocumentResponse]:
    """List documents with optional filtering and stable cursor pagination."""
    query = select(
        KnowledgeDocument,
        func.count(KnowledgeChunk.id).label("chunk_count"),
    ).outerjoin(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
    query = TenantQueryScoper.apply_scope(query, KnowledgeDocument)

    if status:
        query = query.where(KnowledgeDocument.status == status)
    if ingestion_status:
        query = query.where(KnowledgeDocument.ingestion_status == ingestion_status)

    query = query.group_by(KnowledgeDocument.id)

    # Cursor pagination on (created_at, id)
    if cursor:
        cursor_time, cursor_id = decode_cursor(cursor)
        query = query.where(
            (KnowledgeDocument.created_at < cursor_time)
            | (
                (KnowledgeDocument.created_at == cursor_time)
                & (KnowledgeDocument.id < cursor_id)
            )
        )

    query = query.order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
    query = query.limit(limit + 1)

    result = await db.execute(query)
    rows = result.all()

    has_more = len(rows) > limit
    items_to_return = rows[:limit]

    items: list[DocumentResponse] = []
    for doc, chunk_count in items_to_return:
        resp = DocumentResponse(
            id=doc.id,
            title=doc.title,
            source_type=doc.source_type,
            source_filename=doc.source_filename,
            status=doc.status,
            ingestion_status=doc.ingestion_status,
            ingestion_error_code=doc.ingestion_error_code,
            chunk_count=chunk_count,
            created_by_user_id=doc.created_by_user_id,
            published_at=doc.published_at,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
        items.append(resp)

    next_cursor = None
    if has_more and items:
        last = items_to_return[-1][0]
        next_cursor = encode_cursor(last.created_at, last.id)

    return PaginatedResponse(items=items, next_cursor=next_cursor, has_more=has_more)


async def get_document_detail(db: AsyncSession, document_id: uuid.UUID) -> DocumentDetailResponse:
    """Retrieve full document detail including chunks."""
    stmt = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.id == document_id)
        .options(selectinload(KnowledgeDocument.chunks))
    )
    stmt = TenantQueryScoper.apply_scope(stmt, KnowledgeDocument)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    if not doc:
        raise NotFoundError("Knowledge document not found")

    chunk_items = [
        ChunkResponse(
            id=c.id,
            ordinal=c.ordinal,
            content=c.content,
            token_estimate=c.token_estimate,
            created_at=c.created_at,
        )
        for c in doc.chunks
    ]

    return DocumentDetailResponse(
        id=doc.id,
        title=doc.title,
        source_type=doc.source_type,
        source_filename=doc.source_filename,
        status=doc.status,
        ingestion_status=doc.ingestion_status,
        ingestion_error_code=doc.ingestion_error_code,
        chunk_count=len(doc.chunks),
        created_by_user_id=doc.created_by_user_id,
        published_at=doc.published_at,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        content=doc.content,
        chunks=chunk_items,
    )


async def update_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str | None = None,
    content: str | None = None,
) -> KnowledgeDocument:
    """Update title or content for a draft document. Disallows editing published/archived."""
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
    stmt = TenantQueryScoper.apply_scope(stmt, KnowledgeDocument)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    if not doc:
        raise NotFoundError("Knowledge document not found")

    if doc.status != DocumentStatus.draft:
        raise AppException(
            "INVALID_STATUS",
            "Only draft documents can be edited",
            status_code=400,
        )

    if doc.ingestion_status == IngestionStatus.processing:
        raise AppException(
            "INVALID_STATE",
            "Cannot edit a document that is actively processing ingestion",
            status_code=409,
        )

    content_changed = False
    if title is not None and title.strip():
        doc.title = title.strip()
    if content is not None and content.strip() and content != doc.content:
        doc.content = content
        content_changed = True

    await db.commit()

    if content_changed:
        # Re-trigger ingestion for updated content
        await process_document_ingestion(db, doc)

    # Log audit event
    audit = AuditEvent(
        actor_type=ActorType.user,
        actor_id=user_id,
        event_type="document_updated",
        resource_type="knowledge_document",
        resource_id=doc.id,
        metadata_json={"title": doc.title, "content_changed": content_changed},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(doc)
    return doc


async def delete_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Delete draft document. Rejects deleting published or archived documents."""
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
    stmt = TenantQueryScoper.apply_scope(stmt, KnowledgeDocument)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    if not doc:
        raise NotFoundError("Knowledge document not found")

    if doc.status != DocumentStatus.draft:
        raise AppException(
            "INVALID_TRANSITION",
            "Only draft documents may be deleted. Published documents must be archived.",
            status_code=400,
        )

    if doc.ingestion_status == IngestionStatus.processing:
        raise AppException(
            "INVALID_STATE",
            "Cannot delete a document that is actively processing ingestion",
            status_code=409,
        )

    # Log audit event
    audit = AuditEvent(
        actor_type=ActorType.user,
        actor_id=user_id,
        event_type="document_deleted",
        resource_type="knowledge_document",
        resource_id=doc.id,
        metadata_json={"title": doc.title, "status": doc.status.value},
    )
    db.add(audit)
    await db.delete(doc)
    await db.commit()


async def publish_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
) -> KnowledgeDocument:
    """Publish a draft document. Requires status=draft, ingestion_status=ready, chunks > 0."""
    stmt = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.id == document_id)
        .options(selectinload(KnowledgeDocument.chunks))
    )
    stmt = TenantQueryScoper.apply_scope(stmt, KnowledgeDocument)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    if not doc:
        raise NotFoundError("Knowledge document not found")

    if doc.status != DocumentStatus.draft:
        raise AppException(
            "INVALID_TRANSITION",
            f"Cannot publish document with status '{doc.status.value}'. Only draft documents can be published.",
            status_code=400,
        )

    if doc.ingestion_status != IngestionStatus.ready:
        raise AppException(
            "INGESTION_NOT_READY",
            "Cannot publish document until ingestion is ready and embeddings are complete.",
            status_code=400,
        )

    if not doc.chunks:
        raise AppException(
            "NO_CHUNKS",
            "Cannot publish document with no knowledge chunks.",
            status_code=400,
        )

    # State transition: draft -> published
    doc.status = DocumentStatus.published
    doc.published_at = datetime.now(UTC)

    # Log audit event
    audit = AuditEvent(
        actor_type=ActorType.user,
        actor_id=user_id,
        event_type="document_published",
        resource_type="knowledge_document",
        resource_id=doc.id,
        metadata_json={"title": doc.title, "chunk_count": len(doc.chunks)},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(doc)
    return doc


async def archive_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
) -> KnowledgeDocument:
    """Archive a published document. Transitions status from published -> archived."""
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
    stmt = TenantQueryScoper.apply_scope(stmt, KnowledgeDocument)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    if not doc:
        raise NotFoundError("Knowledge document not found")

    if doc.status != DocumentStatus.published:
        raise AppException(
            "INVALID_TRANSITION",
            f"Cannot archive document with status '{doc.status.value}'. Only published documents can be archived.",
            status_code=400,
        )

    # State transition: published -> archived
    doc.status = DocumentStatus.archived

    # Log audit event
    audit = AuditEvent(
        actor_type=ActorType.user,
        actor_id=user_id,
        event_type="document_archived",
        resource_type="knowledge_document",
        resource_id=doc.id,
        metadata_json={"title": doc.title},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(doc)
    return doc


async def retry_document_ingestion(
    db: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    enqueue_background: bool = False,
) -> KnowledgeDocument:
    """Explicit retry of a failed ingestion on a draft document; enqueues background ingestion when enqueue_background=True."""
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
    stmt = TenantQueryScoper.apply_scope(stmt, KnowledgeDocument)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    if not doc:
        raise NotFoundError("Knowledge document not found")

    if doc.status != DocumentStatus.draft:
        raise AppException(
            "INVALID_STATUS",
            "Can only retry ingestion on draft documents",
            status_code=400,
        )

    if doc.ingestion_status != IngestionStatus.failed:
        raise AppException(
            "INVALID_STATUS",
            f"Can only retry ingestion on failed documents, current status is '{doc.ingestion_status.value}'",
            status_code=400,
        )

    # Set document to pending for worker pick-up
    doc.ingestion_status = IngestionStatus.pending
    doc.ingestion_error_code = None

    if enqueue_background:
        await enqueue_ingestion_job(
            db=db,
            document_id=doc.id,
            organization_id=doc.organization_id,
        )

    # Log audit event
    audit = AuditEvent(
        actor_type=ActorType.user,
        actor_id=user_id,
        event_type="document_ingestion_retried",
        resource_type="knowledge_document",
        resource_id=doc.id,
        metadata_json={"title": doc.title, "new_ingestion_status": doc.ingestion_status.value},
    )
    db.add(audit)
    await db.commit()

    if not enqueue_background:
        await process_document_ingestion(db, doc)

    await db.refresh(doc)
    return doc
