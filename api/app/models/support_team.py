from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.tenancy.context import TenantContext

if TYPE_CHECKING:
    from app.models.team_membership import TeamMembership


class SupportTeam(Base):
    __tablename__ = "support_teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_support_teams_organization_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routing_strategy: Mapped[str] = mapped_column(String(32), default="least_busy", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    memberships: Mapped[list[TeamMembership]] = relationship("TeamMembership", back_populates="team", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_support_teams_org", "organization_id"),
    )

