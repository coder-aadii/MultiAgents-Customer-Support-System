"""Phase 6E durable multi-agent workflow checkpoints.

Revision ID: 0015_multi_agent_workflow_tables
Revises: 0014_agent_framework_tables
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_multi_agent_workflow_tables"
down_revision: str | None = "0014_agent_framework_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_jobs.id", ondelete="CASCADE", name="fk_workflow_checkpoints_job_id"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE", name="fk_workflow_checkpoints_conversation_id"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_workflow_checkpoints_tenant_id"),
            nullable=False,
        ),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_step_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completed_tasks", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("blackboard_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'running'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("job_id", name="uq_workflow_checkpoints_job_id"),
    )
    op.create_index("ix_workflow_checkpoints_conversation_id", "workflow_checkpoints", ["conversation_id"])
    op.create_index("ix_workflow_checkpoints_tenant_id", "workflow_checkpoints", ["tenant_id"])
    op.create_index("ix_workflow_checkpoints_plan_id", "workflow_checkpoints", ["plan_id"])
    op.create_index("ix_workflow_checkpoints_status_updated", "workflow_checkpoints", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_workflow_checkpoints_status_updated", table_name="workflow_checkpoints")
    op.drop_index("ix_workflow_checkpoints_plan_id", table_name="workflow_checkpoints")
    op.drop_index("ix_workflow_checkpoints_tenant_id", table_name="workflow_checkpoints")
    op.drop_index("ix_workflow_checkpoints_conversation_id", table_name="workflow_checkpoints")
    op.drop_table("workflow_checkpoints")
