from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import NotFoundError
from app.models.evidence_record import EvidenceRecord
from app.schemas.evidence import EvidenceReference, EvidenceType


def parse_evidence_id(evidence_id: uuid.UUID | str) -> uuid.UUID:
    """Parse raw UUID or 'ref_<uuid>' format into uuid.UUID."""
    if isinstance(evidence_id, uuid.UUID):
        return evidence_id

    val = str(evidence_id).strip()
    if val.startswith("ref_"):
        val = val[4:]

    try:
        return uuid.UUID(val)
    except (ValueError, TypeError) as exc:
        raise NotFoundError("Invalid evidence identifier format") from exc


async def record_evidence(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    evidence_key: str,
    source_type: str | EvidenceType,
    title: str,
    payload_json: dict[str, Any],
    entity_type: str | None = None,
    verified_customer_id: str | None = None,
    expires_in_days: int = 30,
) -> EvidenceRecord:
    """Store or refresh protected evidence record in the database.

    Enforces:
    - Scoping to active conversation_id.
    - Verified customer identity binding.
    - Automatic retention expiration calculation.
    """
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=expires_in_days)
    source_type_str = (
        source_type.value if isinstance(source_type, EvidenceType) else str(source_type)
    )

    # Check for existing record within this conversation
    stmt = (
        select(EvidenceRecord)
        .where(
            EvidenceRecord.conversation_id == conversation_id,
            EvidenceRecord.evidence_key == evidence_key,
        )
        .with_for_update()
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.title = title
        existing.source_type = source_type_str
        existing.entity_type = entity_type
        existing.payload_json = payload_json
        existing.verified_customer_id = verified_customer_id
        existing.expires_at = expires_at
        await db.flush()
        return existing

    record = EvidenceRecord(
        conversation_id=conversation_id,
        evidence_key=evidence_key,
        source_type=source_type_str,
        title=title,
        entity_type=entity_type,
        payload_json=payload_json,
        verified_customer_id=verified_customer_id,
        expires_at=expires_at,
        created_at=now,
    )
    db.add(record)
    await db.flush()
    return record


async def get_evidence_record(
    db: AsyncSession,
    evidence_id: uuid.UUID | str,
    conversation_id: uuid.UUID,
    verified_customer_id: str | None = None,
) -> EvidenceRecord:
    """Retrieve protected evidence record with strict conversation and customer isolation.

    Raises NotFoundError on:
    - Non-existent evidence ID
    - Cross-conversation access attempt
    - Cross-customer access attempt
    - Expired record
    """
    parsed_id = parse_evidence_id(evidence_id)
    now = datetime.now(UTC)

    stmt = select(EvidenceRecord).where(
        EvidenceRecord.id == parsed_id,
        EvidenceRecord.conversation_id == conversation_id,
    )
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()

    if record is None:
        raise NotFoundError("Evidence record not found")

    # Expiration check
    if record.expires_at is not None and record.expires_at <= now:
        raise NotFoundError("Evidence record has expired")

    # Customer identity isolation check
    if record.verified_customer_id is not None:
        if (
            verified_customer_id is None
            or record.verified_customer_id != verified_customer_id
        ):
            # Return 404 to prevent resource discovery
            raise NotFoundError("Evidence record not found")

    return record


async def list_conversation_evidence(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    verified_customer_id: str | None = None,
) -> list[EvidenceRecord]:
    """List all non-expired evidence records belonging to a conversation."""
    now = datetime.now(UTC)
    stmt = (
        select(EvidenceRecord)
        .where(
            EvidenceRecord.conversation_id == conversation_id,
            (EvidenceRecord.expires_at.is_(None)) | (EvidenceRecord.expires_at > now),
        )
        .order_by(EvidenceRecord.created_at.asc())
    )
    result = await db.execute(stmt)
    records = result.scalars().all()

    # Filter by verified customer context if bound
    authorized: list[EvidenceRecord] = []
    for r in records:
        if r.verified_customer_id is not None:
            if verified_customer_id and r.verified_customer_id == verified_customer_id:
                authorized.append(r)
        else:
            authorized.append(r)

    return authorized


def to_public_evidence_reference(record: EvidenceRecord) -> EvidenceReference:
    """Sanitize protected EvidenceRecord into public, opaque EvidenceReference.

    Invariants:
    - Never includes payload_json.
    - Never includes internal customer IDs.
    - Strips all private fields.
    - Retains backward-compatible chunk_id/doc_id for knowledge chunks.
    """
    chunk_id = None
    doc_id = None
    if record.source_type == "knowledge_chunk":
        chunk_id = record.payload_json.get("chunk_id")
        doc_id = record.payload_json.get("doc_id")

    return EvidenceReference(
        evidence_id=f"ref_{record.id}",
        source_type=EvidenceType(record.source_type),
        title=record.title,
        entity_type=record.entity_type,
        chunk_id=str(chunk_id) if chunk_id else None,
        doc_id=str(doc_id) if doc_id else None,
    )


async def delete_conversation_evidence(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    commit: bool = True,
) -> int:
    """Explicitly delete all evidence records for a conversation."""
    stmt = delete(EvidenceRecord).where(EvidenceRecord.conversation_id == conversation_id)
    result = await db.execute(stmt)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return getattr(result, "rowcount", 0)


async def purge_expired_evidence(
    db: AsyncSession,
    commit: bool = True,
) -> int:
    """Purge all expired evidence records across conversations."""
    now = datetime.now(UTC)
    stmt = delete(EvidenceRecord).where(
        EvidenceRecord.expires_at.is_not(None),
        EvidenceRecord.expires_at <= now,
    )
    result = await db.execute(stmt)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return getattr(result, "rowcount", 0)

