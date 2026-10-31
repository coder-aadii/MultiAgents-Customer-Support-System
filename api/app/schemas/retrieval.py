from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=5)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)


class RetrievalResultItem(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    rank: int
    score: float
    content: str
    token_estimate: int
    start_char: int
    end_char: int


class RetrievalResponse(BaseModel):
    query: str
    results: list[RetrievalResultItem]
    total_found: int


class RetrievalDebugResponse(RetrievalResponse):
    threshold: float
    total_chunks_scanned: int
    excluded_unpublished_matches: int
