from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.tenancy.context import TenantContext


class ConnectorType(str, enum.Enum):
    rest = "rest"
    sql = "sql"
    graphql = "graphql"


class AuthType(str, enum.Enum):
    bearer = "bearer"
    basic = "basic"
    api_key = "api_key"
    mtls = "mtls"
    none = "none"


class ConnectorDefinition(Base):
    """External system connector configuration definition."""

    __tablename__ = "connector_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_connector_definitions_organization_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    connector_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    auth_type: Mapped[str] = mapped_column(String(32), default="bearer", nullable=False)
    secret_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

