from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.tenancy.context import TenantContext


class ConnectorAuditLog(Base):
    """Execution telemetry and audit log for enterprise connector invocations.

    Never stores raw customer payload data, secrets, or sensitive PII.
    """

    __tablename__ = "connector_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_connector_audit_logs_organization_id"),
        default=lambda: TenantContext.get_tenant_id(),
        nullable=False,
        index=True,
    )
    connector_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE", name="fk_connector_audit_logs_conversation_id"),
        nullable=False,
    )
    customer_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # success, rate_limited, timeout, error
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )


Index(
    "ix_connector_audit_logs_connector_status",
    ConnectorAuditLog.connector_name,
    ConnectorAuditLog.status,
)

