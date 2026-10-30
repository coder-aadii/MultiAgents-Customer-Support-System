from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.user import User


class HandoffReason(str, enum.Enum):
    visitor_requested = "visitor_requested"
    low_confidence = "low_confidence"
    unsupported_request = "unsupported_request"
    policy = "policy"
    provider_failure = "provider_failure"


class HandoffStatus(str, enum.Enum):
    queued = "queued"
    claimed = "claimed"
    resolved = "resolved"


class Handoff(Base):
    __tablename__ = "handoffs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE", name="fk_handoffs_conversation_id"),
        unique=True,
        nullable=False,
    )
    reason: Mapped[HandoffReason] = mapped_column(
        Enum(HandoffReason, name="handoff_reason"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[HandoffStatus] = mapped_column(
        Enum(HandoffStatus, name="handoff_status"), default=HandoffStatus.queued, nullable=False
    )
    claimed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_handoffs_claimed_by_user_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[Conversation] = relationship()
    claimer: Mapped[User] = relationship()

    __table_args__ = (Index("ix_handoffs_status_created", "status", "created_at"),)

    def __repr__(self) -> str:
        return f"<Handoff(id={self.id}, reason={self.reason}, status={self.status})>"
