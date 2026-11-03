from __future__ import annotations

import asyncio
import io
import os
import re
import unicodedata

import pypdf
import pypdf.errors
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import IngestionStatus, KnowledgeDocument, SourceType
from app.providers import get_embedding_provider
from app.providers.base import ProviderError
from app.services.chunking import split_into_chunks

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
INGESTION_TIMEOUT_SECONDS = 20.0
ALLOWED_EXTENSIONS = {
    ".txt": SourceType.text,
    ".md": SourceType.markdown,
    ".pdf": SourceType.pdf,
}


class IngestionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def sanitize_filename(filename: str | None) -> str:
    """Sanitize filename to prevent directory traversal and remove dangerous characters."""
    if not filename:
        return "untitled.txt"
    basename = os.path.basename(filename).strip()
    clean = re.sub(r"[^\w\.\-\_ ]", "_", basename)
    return clean[:255] or "untitled.txt"


def validate_upload(filename: str | None, raw_bytes: bytes) -> tuple[SourceType, str]:
    """Validate file size, extension, and filename safety before persisting."""
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise IngestionError(
            "FILE_OVERSIZED",
            f"File size exceeds maximum allowed limit of {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )

    clean_filename = sanitize_filename(filename)
    _, ext = os.path.splitext(clean_filename.lower())

    if ext not in ALLOWED_EXTENSIONS:
        raise IngestionError(
            "UNSUPPORTED_TYPE",
            f"Unsupported file extension '{ext}'. Allowed types are .txt, .md, and .pdf",
        )

    source_type = ALLOWED_EXTENSIONS[ext]
    return source_type, clean_filename


def extract_text_from_bytes(source_type: SourceType, raw_bytes: bytes) -> str:
    """Extract and normalize text content from uploaded raw bytes."""
    if source_type in (SourceType.text, SourceType.markdown):
        try:
            raw_text = raw_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            raise IngestionError("PARSER_FAILED", f"Failed to decode text: {e}") from e

        # Normalize line endings and Unicode
        clean = raw_text.replace("\x00", "")
        normalized = unicodedata.normalize("NFC", clean)
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")

        if not normalized.strip():
            raise IngestionError("EMPTY_CONTENT", "Document contains no readable text content")
        return normalized

    if source_type == SourceType.pdf:
        try:
            reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
        except (pypdf.errors.PdfReadError, Exception) as e:
            raise IngestionError("PDF_MALFORMED", f"Malformed or invalid PDF file: {e}") from e

        if reader.is_encrypted:
            raise IngestionError(
                "PDF_ENCRYPTED", "Encrypted or password-protected PDFs are not supported"
            )

        extracted_pages: list[str] = []
        try:
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    extracted_pages.append(page_text)
        except Exception as e:
            raise IngestionError("PARSER_FAILED", f"Failed extracting pages from PDF: {e}") from e

        combined = "\n\n".join(extracted_pages).replace("\x00", "")
        normalized = unicodedata.normalize("NFC", combined)
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")

        if not normalized.strip():
            raise IngestionError(
                "PDF_SCANNED_OR_NO_TEXT",
                "Scanned or image-only PDFs with no extractable text are not supported",
            )
        return normalized

    raise IngestionError("UNSUPPORTED_TYPE", f"Unsupported source type: {source_type}")


async def process_document_ingestion(
    db: AsyncSession,
    document: KnowledgeDocument,
    raw_bytes: bytes | None = None,
) -> None:
    """Synchronous bounded ingestion pipeline.

    Extracts text, chunks it, generates embeddings, and persists chunks idempotently.
    On failure, guarantees no partial chunks remain and records safe error status.
    """
    doc_id = document.id
    doc_source_type = document.source_type
    doc_content = document.content

    document.ingestion_status = IngestionStatus.processing
    document.ingestion_error_code = None
    await db.flush()

    async def _run_ingestion() -> None:
        provider = get_embedding_provider()

        # 1. Extract text if raw bytes provided, otherwise use existing document.content
        if raw_bytes is not None:
            extracted_text = extract_text_from_bytes(doc_source_type, raw_bytes)
            document.content = extracted_text
        else:
            if not (doc_content and doc_content.strip()):
                raise IngestionError("EMPTY_CONTENT", "Document content is empty")
            extracted_text = doc_content

        # 2. Chunk text
        chunks = split_into_chunks(extracted_text, target_tokens=700, overlap_tokens=100)
        if not chunks:
            raise IngestionError("EMPTY_CONTENT", "No chunks generated from document content")

        # 3. Generate embeddings
        chunk_texts = [c.content for c in chunks]
        embeddings = await provider.embed_batch(chunk_texts)

        # Validate embedding dimensions
        for emb in embeddings:
            if len(emb) != 1536:
                raise IngestionError(
                    "PROVIDER_ERROR",
                    f"Embedding dimension mismatch: expected 1536, got {len(emb)}",
                )

        # 4. Atomic chunk persistence: delete stale chunks first (idempotent retry)
        await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id))

        for idx, (chunk_item, emb) in enumerate(zip(chunks, embeddings, strict=True)):
            chunk = KnowledgeChunk(
                document_id=doc_id,
                ordinal=idx,
                content=chunk_item.content,
                token_estimate=chunk_item.token_estimate,
                embedding=emb,
            )
            db.add(chunk)

        document.ingestion_status = IngestionStatus.ready
        document.ingestion_error_code = None
        await db.commit()

    try:
        await asyncio.wait_for(_run_ingestion(), timeout=INGESTION_TIMEOUT_SECONDS)
    except Exception as exc:
        # Roll back uncommitted transaction
        await db.rollback()
        err_code = "PARSER_FAILED"
        if isinstance(exc, IngestionError):
            err_code = exc.code
        elif isinstance(exc, ProviderError):
            err_code = "PROVIDER_ERROR"
        elif isinstance(exc, asyncio.TimeoutError | TimeoutError):
            err_code = "TIMEOUT"
        else:
            err_code = "INTERNAL_ERROR"

        # Safe update without deleting existing usable chunks
        await db.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == doc_id)
            .values(
                ingestion_status=IngestionStatus.failed,
                ingestion_error_code=err_code,
            )
        )
        await db.commit()
