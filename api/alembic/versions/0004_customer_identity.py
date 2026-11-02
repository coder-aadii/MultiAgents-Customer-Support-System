"""Phase 5A.1 Customer identity assurance and session binding schema.

Revision ID: 0004_enterprise_customer_and_evidence
Revises: 0003_knowledge_base
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_customer_identity"
down_revision: str | None = "0003_knowledge_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sub-phase 5A.1: Customer identity assurance columns on visitor_sessions
    op.add_column(
        "visitor_sessions",
        sa.Column("verified_customer_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "visitor_sessions",
        sa.Column("customer_token_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "visitor_sessions",
        sa.Column(
            "identity_assurance_level",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "visitor_sessions",
        sa.Column("customer_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_visitor_sessions_customer_id",
        "visitor_sessions",
        ["verified_customer_id"],
    )
    op.create_index(
        "ix_visitor_sessions_customer_token_hash",
        "visitor_sessions",
        ["customer_token_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_visitor_sessions_customer_token_hash",
        table_name="visitor_sessions",
    )
    op.drop_index(
        "ix_visitor_sessions_customer_id",
        table_name="visitor_sessions",
    )
    op.drop_column("visitor_sessions", "customer_token_expires_at")
    op.drop_column("visitor_sessions", "identity_assurance_level")
    op.drop_column("visitor_sessions", "customer_token_hash")
    op.drop_column("visitor_sessions", "verified_customer_id")
