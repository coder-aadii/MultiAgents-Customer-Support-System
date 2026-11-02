from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.tenancy.context import TenantContext

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.message import Message


class AssistantJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    discarded = "discarded"


class AssistantJob(Base):
    """Durable asynchronous assistant execution job.

    Enforces:
    - 1:1 relationship with visitor messages via unique constraint on message_id.
    - Lease-based locking with worker attribution and lease expiration.
    - Abandoned-job recovery and retry tracking.
    """

    __tablename__ = "assistant_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_assistant_jobs_organization_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE", name="fk_assistant_jobs_conversation_id"),
        nullable=False,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE", name="fk_assistant_jobs_message_id"),
        nullable=False,
    )
    status: Mapped[AssistantJobStatus] = mapped_column(
        String(32),
        default=AssistantJobStatus.queued.value,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[Conversation] = relationship()
    message: Mapped[Message] = relationship()

    __table_args__ = (
        UniqueConstraint("message_id", name="uq_assistant_jobs_message_id"),
        Index("ix_assistant_jobs_status_created", "status", "created_at"),
        Index("ix_assistant_jobs_status_locked", "status", "locked_at"),
        Index("ix_assistant_jobs_conversation_id", "conversation_id"),
    )

    def __repr__(self) -> str:
        return f"<AssistantJob(id={self.id}, status={self.status}, attempts={self.attempts})>"

