from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import DocumentStatus, KnowledgeDocument
from app.providers import get_embedding_provider
from app.schemas.retrieval import RetrievalDebugResponse, RetrievalResponse, RetrievalResultItem
from app.tenancy.context import TenantContext

MAX_PROMPT_CONTEXT_CHARS = 4500
MAX_RETRIEVAL_CHUNKS = 5


async def retrieve_knowledge_chunks(
    db: AsyncSession,
    query: str,
    limit: int = 5,
    min_score: float | None = None,
    is_debug: bool = False,
    organization_id: uuid.UUID | None = None,
) -> RetrievalResponse | RetrievalDebugResponse:
    """Execute vector cosine similarity retrieval against published documents only.

    Enforces:
    1. Published-document filter first (status=published).
    2. Cosine similarity threshold second (default 0.72).
    3. Top 5 maximum results with deterministic tie-break (doc_id, ordinal).
    4. 4,500 character context cap retaining top-ranked chunks only.
    5. Clean DTO results containing citations, scores, and offsets (never raw vectors).
    """
    clean_query = query.strip()
    effective_threshold = (
        min_score if min_score is not None else settings.RETRIEVAL_MIN_SCORE
    )
    effective_limit = min(max(1, limit), MAX_RETRIEVAL_CHUNKS)

    provider = get_embedding_provider()
    query_vector = await provider.embed_text(clean_query)

    # Cosine distance: 0 is identical, 2 is opposite.
    # Similarity = 1.0 - distance
    distance_expr = KnowledgeChunk.embedding.cosine_distance(query_vector)

    effective_org_id = organization_id or TenantContext.get_tenant_id()

    # Published documents query with HNSW-optimized top-k limit (removes massive doc_full_content from initial scan)
    stmt = (
        select(
            KnowledgeChunk.id.label("chunk_id"),
            KnowledgeChunk.content.label("chunk_content"),
            KnowledgeChunk.token_estimate.label("chunk_token_estimate"),
            KnowledgeDocument.id.label("doc_id"),
            KnowledgeDocument.title.label("doc_title"),
            distance_expr.label("distance"),
        )
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeDocument.status == DocumentStatus.published,
            KnowledgeDocument.organization_id == effective_org_id,
            KnowledgeChunk.organization_id == effective_org_id,
        )
        .order_by(
            distance_expr.asc(),
            KnowledgeDocument.id.asc(),
            KnowledgeChunk.ordinal.asc(),
        )
        .limit(50)  # Guarantees HNSW index graph traversal; bounds scan
    )

    result = await db.execute(stmt)
    rows = result.all()

    # First pass: filter by threshold and context character cap
    candidate_items: list[dict] = []
    total_accumulated_chars = 0
    doc_ids_to_fetch: set[uuid.UUID] = set()

    for row in rows:
        dist: float | None = row.distance
        raw_sim = 1.0 - float(dist) if dist is not None else 0.0
        similarity = round(max(0.0, min(1.0, raw_sim)), 4)

        if similarity < effective_threshold:
            continue

        chunk_text = row.chunk_content
        if total_accumulated_chars + len(chunk_text) > MAX_PROMPT_CONTEXT_CHARS and candidate_items:
            break

        doc_ids_to_fetch.add(row.doc_id)
        candidate_items.append({
            "chunk_id": row.chunk_id,
            "chunk_content": chunk_text,
            "chunk_token_estimate": row.chunk_token_estimate,
            "doc_id": row.doc_id,
            "doc_title": row.doc_title,
            "similarity": similarity,
        })
        total_accumulated_chars += len(chunk_text)

        if len(candidate_items) >= effective_limit:
            break

    # Second pass: fetch parent document content only for the <= 5 final matched documents to compute offsets
    doc_contents: dict[uuid.UUID, str] = {}
    if doc_ids_to_fetch:
        docs_res = await db.execute(
            select(KnowledgeDocument.id, KnowledgeDocument.content).where(
                KnowledgeDocument.id.in_(doc_ids_to_fetch)
            )
        )
        doc_contents = {row[0]: row[1] for row in docs_res.all()}

    items: list[RetrievalResultItem] = []
    for rank, cand in enumerate(candidate_items, start=1):
        chunk_text = cand["chunk_content"]
        parent_content = doc_contents.get(cand["doc_id"], "")

        start_char = 0
        end_char = len(chunk_text)
        if parent_content:
            pos = parent_content.find(chunk_text[:min(60, len(chunk_text))])
            if pos != -1:
                start_char = pos
                end_char = min(len(parent_content), start_char + len(chunk_text))

        items.append(
            RetrievalResultItem(
                chunk_id=cand["chunk_id"],
                document_id=cand["doc_id"],
                document_title=cand["doc_title"],
                rank=rank,
                score=cand["similarity"],
                content=chunk_text,
                token_estimate=cand["chunk_token_estimate"],
                start_char=start_char,
                end_char=end_char,
            )
        )

    if not is_debug:
        return RetrievalResponse(
            query=clean_query,
            results=items,
            total_found=len(items),
        )

    # For debug view: calculate unpublished matches that would have matched
    unpub_stmt = (
        select(func.count(KnowledgeChunk.id))
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeDocument.status != DocumentStatus.published,
            (1.0 - distance_expr) >= effective_threshold,
        )
    )
    unpub_res = await db.execute(unpub_stmt)
    excluded_count = unpub_res.scalar() or 0

    total_chunks_stmt = select(func.count(KnowledgeChunk.id))
    total_chunks_res = await db.execute(total_chunks_stmt)
    total_chunks = total_chunks_res.scalar() or 0

    return RetrievalDebugResponse(
        query=clean_query,
        results=items,
        total_found=len(items),
        threshold=effective_threshold,
        total_chunks_scanned=total_chunks,
        excluded_unpublished_matches=excluded_count,
    )
