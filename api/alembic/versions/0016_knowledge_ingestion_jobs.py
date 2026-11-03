"""Stage 2: Durable background knowledge ingestion jobs.

Revision ID: 0016_knowledge_ingestion_jobs
Revises: 0015_multi_agent_workflow_tables
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_knowledge_ingestion_jobs"
down_revision: str | None = "0015_multi_agent_workflow_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_knowledge_ingestion_jobs_organization_id"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE", name="fk_knowledge_ingestion_jobs_document_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_knowledge_ingestion_jobs_organization_id",
        "knowledge_ingestion_jobs",
        ["organization_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_document_id",
        "knowledge_ingestion_jobs",
        ["document_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_status_created",
        "knowledge_ingestion_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_status_locked",
        "knowledge_ingestion_jobs",
        ["status", "locked_at"],
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_org_doc",
        "knowledge_ingestion_jobs",
        ["organization_id", "document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_ingestion_jobs_org_doc", table_name="knowledge_ingestion_jobs")
    op.drop_index("ix_knowledge_ingestion_jobs_status_locked", table_name="knowledge_ingestion_jobs")
    op.drop_index("ix_knowledge_ingestion_jobs_status_created", table_name="knowledge_ingestion_jobs")
    op.drop_index("ix_knowledge_ingestion_jobs_document_id", table_name="knowledge_ingestion_jobs")
    op.drop_index("ix_knowledge_ingestion_jobs_organization_id", table_name="knowledge_ingestion_jobs")
    op.drop_table("knowledge_ingestion_jobs")

