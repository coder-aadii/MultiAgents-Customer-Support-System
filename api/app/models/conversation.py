from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, desc
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.visitor import Visitor


class ConversationStatus(str, enum.Enum):
    open = "open"
    waiting_for_staff = "waiting_for_staff"
    staff_active = "staff_active"
    closed = "closed"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visitor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("visitors.id", name="fk_conversations_visitor_id"),
        nullable=False,
    )
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status"),
        default=ConversationStatus.open,
        nullable=False,
    )
    assigned_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_conversations_assigned_user_id"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(160), nullable=True)
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    visitor: Mapped[Visitor] = relationship()
    assignee: Mapped[User] = relationship()

    __table_args__ = (
        Index("ix_conversations_status_last_message", "status", desc("last_message_at")),
        Index("ix_conversations_visitor_created", "visitor_id", desc("created_at")),
    )

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, status={self.status})>"
