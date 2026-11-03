from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.tenancy.context import TenantContext

if TYPE_CHECKING:
    from app.models.knowledge_document import KnowledgeDocument


class IngestionJobStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class KnowledgeIngestionJob(Base):
    """Durable tenant-scoped knowledge document ingestion job.

    Enforces:
    - Durable job records linked to a specific knowledge document.
    - Lease-based locking with worker attribution, leasing duration, and crash recovery.
    - Bounded retries and sanitized failure recording.
    - Tenant isolation via organization_id.
    """

    __tablename__ = "knowledge_ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_knowledge_ingestion_jobs_organization_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE", name="fk_knowledge_ingestion_jobs_document_id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=IngestionJobStatus.queued.value,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[KnowledgeDocument] = relationship()

    __table_args__ = (
        Index("ix_knowledge_ingestion_jobs_status_created", "status", "created_at"),
        Index("ix_knowledge_ingestion_jobs_status_locked", "status", "locked_at"),
        Index("ix_knowledge_ingestion_jobs_org_doc", "organization_id", "document_id"),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeIngestionJob(id={self.id}, doc={self.document_id}, status={self.status}, attempts={self.attempts})>"

