from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.knowledge_document import DocumentStatus, IngestionStatus, SourceType


class DocumentCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    source_type: SourceType = SourceType.text
    content: str = Field(min_length=1, max_length=5 * 1024 * 1024)


class DocumentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=5 * 1024 * 1024)


class ChunkResponse(BaseModel):
    id: uuid.UUID
    ordinal: int
    content: str
    token_estimate: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentResponse(BaseModel):
    id: uuid.UUID
    title: str
    source_type: SourceType
    source_filename: str | None
    content_hash: str | None = None
    status: DocumentStatus
    ingestion_status: IngestionStatus
    ingestion_error_code: str | None
    chunk_count: int = 0
    created_by_user_id: uuid.UUID
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentDetailResponse(DocumentResponse):
    content: str
    chunks: list[ChunkResponse] = []
