"""Phase 5A.4 Durable asynchronous assistant jobs schema.

Revision ID: 0006_assistant_jobs
Revises: 0005_evidence_records
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_assistant_jobs"
down_revision: str | None = "0005_evidence_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE", name="fk_assistant_jobs_conversation_id"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE", name="fk_assistant_jobs_message_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("message_id", name="uq_assistant_jobs_message_id"),
    )

    op.create_index(
        "ix_assistant_jobs_status_created",
        "assistant_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_assistant_jobs_status_locked",
        "assistant_jobs",
        ["status", "locked_at"],
    )
    op.create_index(
        "ix_assistant_jobs_conversation_id",
        "assistant_jobs",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_jobs_conversation_id", table_name="assistant_jobs")
    op.drop_index("ix_assistant_jobs_status_locked", table_name="assistant_jobs")
    op.drop_index("ix_assistant_jobs_status_created", table_name="assistant_jobs")
    op.drop_table("assistant_jobs")

