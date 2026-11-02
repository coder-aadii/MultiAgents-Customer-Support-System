from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.tenancy.context import TenantContext


class SLAPolicy(Base):
    __tablename__ = "sla_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_sla_policies_org_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    first_response_target_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_target_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    business_hours_schedule_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

