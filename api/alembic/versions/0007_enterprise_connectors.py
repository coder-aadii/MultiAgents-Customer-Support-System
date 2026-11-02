"""Phase 5B Enterprise connectors schema and audit telemetry.

Revision ID: 0007_enterprise_connectors
Revises: 0006_assistant_jobs
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_enterprise_connectors"
down_revision: str | None = "0006_assistant_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. connector_definitions
    op.create_table(
        "connector_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("connector_type", sa.String(32), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("base_url", sa.String(255), nullable=True),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("auth_type", sa.String(32), nullable=False, server_default="bearer"),
        sa.Column("secret_reference", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_connector_definitions_name", "connector_definitions", ["name"])

    # 2. connector_audit_logs
    op.create_table(
        "connector_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("connector_name", sa.String(64), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE", name="fk_connector_audit_logs_conversation_id"),
            nullable=False,
        ),
        sa.Column("customer_id", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("execution_time_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_connector_audit_logs_connector_name", "connector_audit_logs", ["connector_name"])
    op.create_index("ix_connector_audit_logs_customer_id", "connector_audit_logs", ["customer_id"])
    op.create_index("ix_connector_audit_logs_created_at", "connector_audit_logs", ["created_at"])
    op.create_index(
        "ix_connector_audit_logs_connector_status",
        "connector_audit_logs",
        ["connector_name", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_connector_audit_logs_connector_status", table_name="connector_audit_logs")
    op.drop_index("ix_connector_audit_logs_created_at", table_name="connector_audit_logs")
    op.drop_index("ix_connector_audit_logs_customer_id", table_name="connector_audit_logs")
    op.drop_index("ix_connector_audit_logs_connector_name", table_name="connector_audit_logs")
    op.drop_table("connector_audit_logs")

    op.drop_index("ix_connector_definitions_name", table_name="connector_definitions")
    op.drop_table("connector_definitions")

