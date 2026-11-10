"""Add content_hash to knowledge_documents for duplicate prevention.

Revision ID: 0017_knowledge_content_hash
Revises: 0016_knowledge_ingestion_jobs
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_knowledge_content_hash"
down_revision: str | None = "0016_knowledge_ingestion_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column("content_hash", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_knowledge_documents_org_hash",
        "knowledge_documents",
        ["organization_id", "content_hash"],
        unique=False,
    )
    op.create_index(
        "uq_knowledge_documents_org_active_hash",
        "knowledge_documents",
        ["organization_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("status IN ('draft', 'published') AND content_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_knowledge_documents_org_active_hash", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_org_hash", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "content_hash")

