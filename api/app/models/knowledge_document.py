from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.tenancy.context import TenantContext

if TYPE_CHECKING:
    from app.models.knowledge_chunk import KnowledgeChunk
    from app.models.user import User


class DocumentStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class SourceType(str, enum.Enum):
    text = "text"
    markdown = "markdown"
    pdf = "pdf"


class IngestionStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_knowledge_documents_organization_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type_enum"), nullable=False
    )
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status_enum"),
        default=DocumentStatus.draft,
        nullable=False,
    )
    ingestion_status: Mapped[IngestionStatus] = mapped_column(
        Enum(IngestionStatus, name="ingestion_status_enum"),
        default=IngestionStatus.pending,
        nullable=False,
    )
    ingestion_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    creator: Mapped[User] = relationship()
    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="KnowledgeChunk.ordinal"
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeDocument(id={self.id}, title={self.title}, "
            f"status={self.status}, ingestion_status={self.ingestion_status})>"
        )
