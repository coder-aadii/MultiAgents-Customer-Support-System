"""Phase 5D Add organization_id column to core tables and backfill default org.

Revision ID: 0010_tenant_scoping_columns
Revises: 0009_multi_tenant_organizations
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_tenant_scoping_columns"
down_revision: str | None = "0009_multi_tenant_organizations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"

TENANT_TABLES = [
    "users",
    "widget_settings",
    "visitors",
    "visitor_sessions",
    "conversations",
    "messages",
    "knowledge_documents",
    "knowledge_chunks",
    "conversation_evidence_records",
    "business_actions",
    "audit_events",
    "assistant_jobs",
    "connector_definitions",
    "connector_audit_logs",
]


def upgrade() -> None:
    conn = op.get_bind()

    for table in TENANT_TABLES:
        if table == "audit_events":
            op.execute("ALTER TABLE audit_events DISABLE TRIGGER trg_audit_events_immutable;")

        # 1. Add column with server_default
        op.add_column(
            table,
            sa.Column(
                "organization_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "organizations.id",
                    ondelete="CASCADE",
                    name=f"fk_{table}_organization_id",
                ),
                nullable=True,
                server_default=sa.text(f"'{DEFAULT_ORG_ID}'::uuid"),
            ),
        )

        # 2. Backfill with default organization ID
        conn.execute(
            sa.text(
                f"UPDATE {table} SET organization_id = CAST(:org_id AS uuid) WHERE organization_id IS NULL;"
            ),
            {"org_id": DEFAULT_ORG_ID},
        )

        # 3. Set NOT NULL
        op.alter_column(table, "organization_id", nullable=False)

        # 4. Add index
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])

        if table == "audit_events":
            op.execute("ALTER TABLE audit_events ENABLE TRIGGER trg_audit_events_immutable;")


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_index(f"ix_{table}_organization_id", table_name=table)
        op.drop_constraint(f"fk_{table}_organization_id", table, type_="foreignkey")
        op.drop_column(table, "organization_id")
