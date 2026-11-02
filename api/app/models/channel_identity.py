from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.tenancy.context import TenantContext


class ChannelIdentity(Base):
    __tablename__ = "channel_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_channel_identities_organization_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
    )
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    visitor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("visitors.id", ondelete="CASCADE", name="fk_channel_identities_visitor_id"),
        nullable=False,
    )
    profile_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "channel_type", "external_user_id", name="uq_channel_identities_org_channel_user"),
        Index("ix_channel_identities_lookup", "organization_id", "channel_type", "external_user_id"),
    )

