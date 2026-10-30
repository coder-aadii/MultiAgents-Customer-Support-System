from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.visitor import Visitor


class VisitorSession(Base):
    __tablename__ = "visitor_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visitor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("visitors.id", ondelete="CASCADE", name="fk_visitor_sessions_visitor_id"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "conversations.id", ondelete="CASCADE", name="fk_visitor_sessions_conversation_id"
        ),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    origin: Mapped[str] = mapped_column(String(2048), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    visitor: Mapped[Visitor] = relationship()
    conversation: Mapped[Conversation] = relationship()

    __table_args__ = (
        Index("ix_visitor_sessions_token_hash", "token_hash", unique=True),
        Index("ix_visitor_sessions_visitor_id", "visitor_id"),
        Index("ix_visitor_sessions_conversation_id", "conversation_id"),
        Index("ix_visitor_sessions_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<VisitorSession(id={self.id}, visitor_id={self.visitor_id})>"
