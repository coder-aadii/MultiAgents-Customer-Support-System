from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.tenancy.context import TenantContext


class ActionRiskLevel(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ActionStatus(str, enum.Enum):
    drafted = "drafted"
    awaiting_confirmation = "awaiting_confirmation"
    awaiting_approval = "awaiting_approval"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    rejected = "rejected"
    expired = "expired"
    cancelled = "cancelled"


class BusinessAction(Base):
    """Tracks state-changing business action proposals, confirmations, approvals, and execution."""

    __tablename__ = "business_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
            name="fk_business_actions_organization_id",
        ),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey(
            "conversations.id", ondelete="CASCADE", name="fk_business_actions_conversation_id"
        ),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[str] = mapped_column(sa.String(128), nullable=False, index=True)
    action_name: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(sa.String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, default=ActionStatus.drafted.value, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(
        sa.String(64), unique=True, nullable=False, index=True
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    summary_message: Mapped[str] = mapped_column(sa.Text, nullable=False)
    required_confirmation_phrase: Mapped[str] = mapped_column(sa.String(128), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_business_actions_reviewed_by_user_id"
        ),
        nullable=True,
        index=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    execution_attempts: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    audit_entries: Mapped[list[ActionAuditJournal]] = relationship(
        "ActionAuditJournal",
        back_populates="business_action",
        cascade="all, delete-orphan",
        order_by="ActionAuditJournal.created_at",
    )

