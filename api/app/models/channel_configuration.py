from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.tenancy.context import TenantContext


class ChannelConfiguration(Base):
    __tablename__ = "channel_configurations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_channel_configurations_organization_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
    )
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    webhook_secret_enc: Mapped[str] = mapped_column(String(255), nullable=False)
    api_credentials_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    channel_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "channel_type", name="uq_channel_configurations_org_type"),
        Index("ix_channel_configurations_org_type", "organization_id", "channel_type"),
    )

