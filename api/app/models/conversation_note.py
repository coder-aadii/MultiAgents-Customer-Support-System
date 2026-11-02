from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.tenancy.context import TenantContext

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.user import User


class ConversationInternalNote(Base):
    __tablename__ = "conversation_internal_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_conversation_notes_org_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE", name="fk_conversation_notes_conv_id"),
        nullable=False,
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_conversation_notes_author_id"),
        nullable=False,
    )
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_whisper: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    conversation: Mapped[Conversation] = relationship("Conversation")
    author: Mapped[User] = relationship("User")

    __table_args__ = (
        Index("ix_conversation_notes_conv", "conversation_id"),
    )

