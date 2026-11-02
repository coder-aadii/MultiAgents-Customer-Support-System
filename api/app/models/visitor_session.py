from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.tenancy.context import TenantContext

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.visitor import Visitor


class VisitorSession(Base):
    __tablename__ = "visitor_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_visitor_sessions_organization_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
        index=True,
    )
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

    # Customer identity assurance & verification (Phase 5A)
    verified_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    customer_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    identity_assurance_level: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    customer_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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
        Index("ix_visitor_sessions_customer_id", "verified_customer_id"),
        Index("ix_visitor_sessions_customer_token_hash", "customer_token_hash"),
    )

    @property
    def is_customer_authenticated(self) -> bool:
        """Return True only if session is IAL 2, has verified customer id, and token is not expired."""
        if self.identity_assurance_level < 2 or not self.verified_customer_id:
            return False
        if self.customer_token_expires_at is not None:
            now = datetime.now(UTC)
            if self.customer_token_expires_at <= now:
                return False
        return True

    @property
    def effective_identity_assurance_level(self) -> int:
        """Effective IAL drops to 0 immediately upon token expiration."""
        if not self.is_customer_authenticated:
            return 0
        return self.identity_assurance_level

    def __repr__(self) -> str:
        return f"<VisitorSession(id={self.id}, visitor_id={self.visitor_id}, ial={self.effective_identity_assurance_level})>"
