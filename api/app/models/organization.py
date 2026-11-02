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
    from app.models.user import User


class OrgMemberRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    supervisor = "supervisor"
    agent = "agent"


class Organization(Base):
    """First-class enterprise tenant organization governing all isolated data and users."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    sso_config_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    memberships: Mapped[list[OrganizationMembership]] = relationship(
        "OrganizationMembership",
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class OrganizationMembership(Base):
    """Role-based access binding between a User and an Organization."""

    __tablename__ = "organization_memberships"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_org_memberships_org_id"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_org_memberships_user_id"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        sa.String(32), default=OrgMemberRole.agent.value, nullable=False, index=True
    )
    permissions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    organization: Mapped[Organization] = relationship(
        "Organization", back_populates="memberships"
    )
    user: Mapped[User] = relationship("User")

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_org_membership_org_user"
        ),
    )

