from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.business_action import BusinessAction


class ActionAuditEventType(str, enum.Enum):
    proposed = "proposed"
    confirmed = "confirmed"
    approved = "approved"
    rejected = "rejected"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    expired = "expired"
    reconciled = "reconciled"


class ActionActorType(str, enum.Enum):
    customer = "customer"
    staff = "staff"
    system = "system"


class ActionAuditJournal(Base):
    """Immutable audit journal tracking every state transition and actor interaction for business actions."""

    __tablename__ = "action_audit_journal"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey(
            "business_actions.id", ondelete="CASCADE", name="fk_action_audit_journal_action_id"
        ),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )

    business_action: Mapped[BusinessAction] = relationship(
        "BusinessAction", back_populates="audit_entries"
    )

