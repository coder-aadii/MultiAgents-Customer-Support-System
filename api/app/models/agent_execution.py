"""Durable, sanitized audit journal for Phase 6 agent executions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.common.redaction import sanitize_audit_payload
from app.models.base import Base


class AgentExecutionAudit(Base):
    __tablename__ = "agent_execution_audits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_agent_execution_audits_tenant_id"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE", name="fk_agent_execution_audits_conversation_id"),
        nullable=False,
        index=True,
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_version: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_phase: Mapped[str] = mapped_column(String(32), nullable=False)
    input_payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    token_usage: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    execution_duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        Index("ix_agent_execution_audits_created_at", "created_at"),
    )

    @validates("input_payload_json", "output_payload_json")
    def sanitize_payload(self, _key: str, payload: dict[str, Any]) -> dict[str, Any]:
        return sanitize_audit_payload(payload)
