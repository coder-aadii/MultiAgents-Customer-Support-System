"""Phase 6A agent framework execution audit journal.

Revision ID: 0014_agent_framework_tables
Revises: 0013_support_operations
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_agent_framework_tables"
down_revision: str | None = "0013_support_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_execution_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_agent_execution_audits_tenant_id"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE", name="fk_agent_execution_audits_conversation_id"),
            nullable=False,
        ),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("agent_version", sa.String(16), nullable=False),
        sa.Column("execution_phase", sa.String(32), nullable=False),
        sa.Column("input_payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("token_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("execution_duration_ms", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_agent_execution_audits_tenant_id", "agent_execution_audits", ["tenant_id"])
    op.create_index(
        "ix_agent_execution_audits_conversation_id", "agent_execution_audits", ["conversation_id"]
    )
    op.create_index("ix_agent_execution_audits_agent_name", "agent_execution_audits", ["agent_name"])
    op.create_index("ix_agent_execution_audits_created_at", "agent_execution_audits", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_execution_audits_created_at", table_name="agent_execution_audits")
    op.drop_index("ix_agent_execution_audits_agent_name", table_name="agent_execution_audits")
    op.drop_index("ix_agent_execution_audits_conversation_id", table_name="agent_execution_audits")
    op.drop_index("ix_agent_execution_audits_tenant_id", table_name="agent_execution_audits")
    op.drop_table("agent_execution_audits")
