from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.tenancy.context import TenantContext

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.user import User


class SenderType(str, enum.Enum):
    visitor = "visitor"
    assistant = "assistant"
    staff = "staff"
    system = "system"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_messages_organization_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE", name="fk_messages_conversation_id"),
        nullable=False,
    )
    client_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    sender_type: Mapped[SenderType] = mapped_column(
        Enum(SenderType, name="sender_type"), nullable=False
    )
    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", name="fk_messages_sender_user_id"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    channel_type: Mapped[str] = mapped_column(
        Text, default="widget", nullable=False
    )
    external_message_id: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    delivery_status: Mapped[str] = mapped_column(
        Text, default="delivered", nullable=False
    )
    delivery_error_code: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    conversation: Mapped[Conversation] = relationship()
    sender: Mapped[User] = relationship()

    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_messages_conversation_client_message_id",
        ),
    )

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, type={self.sender_type})>"
