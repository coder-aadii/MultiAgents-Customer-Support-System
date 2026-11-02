from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class EvidenceRecord(Base):
    """Protected server-side registry of retrieved entity and knowledge evidence.

    Invariants:
    - Never exposed directly to the public widget API.
    - Scoped to conversation_id and optionally verified_customer_id.
    - Cascades on conversation deletion.
    - Automatically subject to expiration policies via expires_at.
    """

    __tablename__ = "conversation_evidence_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE",
            name="fk_conversation_evidence_records_conversation_id",
        ),
        nullable=False,
    )
    evidence_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    verified_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    conversation: Mapped[Conversation] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "evidence_key", name="uq_conversation_evidence_key"
        ),
        Index("ix_conversation_evidence_records_conversation_id", "conversation_id"),
        Index("ix_conversation_evidence_records_customer_id", "verified_customer_id"),
        Index("ix_conversation_evidence_records_expires_at", "expires_at"),
        Index("ix_conversation_evidence_records_created_at", "created_at"),
    )

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= datetime.now(UTC)

    def __repr__(self) -> str:
        return (
            f"<EvidenceRecord(id={self.id}, key={self.evidence_key}, "
            f"type={self.source_type}, customer={self.verified_customer_id})>"
        )

