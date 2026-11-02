from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.tenancy.context import TenantContext


class CSATSurvey(Base):
    __tablename__ = "csat_surveys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_csat_surveys_org_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE", name="fk_csat_surveys_conv_id"),
        unique=True,
        nullable=False,
    )
    customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 to 5
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

