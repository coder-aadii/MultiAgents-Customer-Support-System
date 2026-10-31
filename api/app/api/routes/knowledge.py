from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import AppException
from app.database import get_session
from app.dependencies import require_admin, require_staff
from app.models.knowledge_document import DocumentStatus, IngestionStatus
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.knowledge import (
    DocumentCreateRequest,
    DocumentDetailResponse,
    DocumentResponse,
    DocumentUpdateRequest,
)
from app.schemas.retrieval import (
    RetrievalDebugResponse,
    RetrievalRequest,
    RetrievalResponse,
)
from app.services.ingestion_service import (
    IngestionError,
    extract_text_from_bytes,
    validate_upload,
)
from app.services.knowledge_service import (
    archive_document,
    create_document,
    delete_document,
    get_document_detail,
    list_documents,
    publish_document,
    retry_document_ingestion,
    update_document,
)
from app.services.retrieval_service import retrieve_knowledge_chunks

knowledge_router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@knowledge_router.get("/documents", response_model=PaginatedResponse[DocumentResponse])
async def list_knowledge_documents(
    status: DocumentStatus | None = Query(default=None),
    ingestion_status: IngestionStatus | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> PaginatedResponse[DocumentResponse]:
    """List knowledge documents with status filtering and cursor pagination."""
    return await list_documents(
        db=db,
        status=status,
        ingestion_status=ingestion_status,
        cursor=cursor,
        limit=limit,
    )


@knowledge_router.post(
    "/documents",
    response_model=DocumentDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_document(
    payload: DocumentCreateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> DocumentDetailResponse:
    """Create a draft knowledge document from text or markdown content."""
    doc = await create_document(
        db=db,
        title=payload.title,
        source_type=payload.source_type,
        content=payload.content,
        user_id=current_user.id,
    )
    return await get_document_detail(db, doc.id)


@knowledge_router.post(
    "/documents/upload",
    response_model=DocumentDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_knowledge_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> DocumentDetailResponse:
    """Upload a TXT, Markdown, or PDF file to create a draft knowledge document."""
    try:
        raw_bytes = await file.read()
        source_type, clean_filename = validate_upload(file.filename, raw_bytes)
        doc_title = title.strip() if title and title.strip() else clean_filename

        # Extract text from raw bytes
        content = extract_text_from_bytes(source_type, raw_bytes)
    except IngestionError as e:
        raise AppException(e.code, e.message, status_code=400) from e

    doc = await create_document(
        db=db,
        title=doc_title,
        source_type=source_type,
        content=content,
        user_id=current_user.id,
        source_filename=clean_filename,
        raw_bytes=None,  # Pre-extracted content avoids duplicate parsing overhead
    )
    return await get_document_detail(db, doc.id)


@knowledge_router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
async def get_knowledge_document(
    document_id: uuid.UUID,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> DocumentDetailResponse:
    """Get full document detail and chunk listing."""
    return await get_document_detail(db, document_id)


@knowledge_router.patch("/documents/{document_id}", response_model=DocumentDetailResponse)
async def update_knowledge_document(
    document_id: uuid.UUID,
    payload: DocumentUpdateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> DocumentDetailResponse:
    """Update title or content of a draft knowledge document."""
    doc = await update_document(
        db=db,
        document_id=document_id,
        user_id=current_user.id,
        title=payload.title,
        content=payload.content,
    )
    return await get_document_detail(db, doc.id)


@knowledge_router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_knowledge_document(
    document_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Delete a draft knowledge document. Rejects non-draft documents."""
    await delete_document(db=db, document_id=document_id, user_id=current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@knowledge_router.post("/documents/{document_id}/publish", response_model=DocumentDetailResponse)
async def publish_knowledge_document(
    document_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> DocumentDetailResponse:
    """Publish a draft document. Guarded: only if ingestion_status=ready and chunks exist."""
    doc = await publish_document(db=db, document_id=document_id, user_id=current_user.id)
    return await get_document_detail(db, doc.id)


@knowledge_router.post("/documents/{document_id}/archive", response_model=DocumentDetailResponse)
async def archive_knowledge_document(
    document_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> DocumentDetailResponse:
    """Archive a published document."""
    doc = await archive_document(db=db, document_id=document_id, user_id=current_user.id)
    return await get_document_detail(db, doc.id)


@knowledge_router.post("/documents/{document_id}/retry", response_model=DocumentDetailResponse)
async def retry_knowledge_document_ingestion(
    document_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> DocumentDetailResponse:
    """Explicitly retry failed ingestion on a draft document."""
    doc = await retry_document_ingestion(db=db, document_id=document_id, user_id=current_user.id)
    return await get_document_detail(db, doc.id)


@knowledge_router.post("/retrieve", response_model=RetrievalResponse)
@knowledge_router.post("/documents/retrieve", response_model=RetrievalResponse, include_in_schema=False)
async def retrieve_published_knowledge(
    payload: RetrievalRequest,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> RetrievalResponse:
    """Retrieve top relevant knowledge chunks from published documents only.

    Restricted to staff and admin. Excludes draft and archived content.
    """
    resp = await retrieve_knowledge_chunks(
        db=db,
        query=payload.query,
        limit=payload.limit,
        min_score=payload.min_score,
        is_debug=False,
    )
    assert isinstance(resp, RetrievalResponse)
    return resp


@knowledge_router.post("/retrieve/debug", response_model=RetrievalDebugResponse)
@knowledge_router.post(
    "/documents/retrieve/debug",
    response_model=RetrievalDebugResponse,
    include_in_schema=False,
)
async def retrieve_knowledge_debug(
    payload: RetrievalRequest,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> RetrievalDebugResponse:
    """Retrieve relevant chunks with diagnostic metrics for threshold and filtering inspection.

    Restricted to staff and admin per master specification.
    """
    resp = await retrieve_knowledge_chunks(
        db=db,
        query=payload.query,
        limit=payload.limit,
        min_score=payload.min_score,
        is_debug=True,
    )
    assert isinstance(resp, RetrievalDebugResponse)
    return resp
