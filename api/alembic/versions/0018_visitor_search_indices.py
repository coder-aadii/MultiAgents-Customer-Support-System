"""Add trigram search indices for visitors.

Revision ID: 0018_visitor_search_indices
Revises: 0017_knowledge_content_hash
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_visitor_search_indices"
down_revision: str | None = "0017_knowledge_content_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_visitors_name_trgm ON visitors USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_visitors_email_trgm ON visitors USING gin (email gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_visitors_email_trgm")
    op.execute("DROP INDEX IF EXISTS ix_visitors_name_trgm")

