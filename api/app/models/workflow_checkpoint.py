"""Durable checkpoint state for resumable Phase 6 multi-agent workflows."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorkflowCheckpointStatus(str, enum.Enum):
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    preempted = "preempted"


class WorkflowCheckpoint(Base):
    """One tenant-scoped durable snapshot per assistant job."""

    __tablename__ = "workflow_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_jobs.id", ondelete="CASCADE", name="fk_workflow_checkpoints_job_id"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE", name="fk_workflow_checkpoints_conversation_id"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_workflow_checkpoints_tenant_id"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    current_step_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_tasks: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    blackboard_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=WorkflowCheckpointStatus.running.value, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("job_id", name="uq_workflow_checkpoints_job_id"),
        Index("ix_workflow_checkpoints_status_updated", "status", "updated_at"),
    )
