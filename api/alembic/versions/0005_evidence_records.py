"""Phase 5A.2 Protected evidence records schema.

Revision ID: 0005_evidence_records
Revises: 0004_customer_identity
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_evidence_records"
down_revision: str | None = "0004_customer_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_evidence_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_key", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("verified_customer_id", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "evidence_key",
            name="uq_conversation_evidence_key",
        ),
    )
    op.create_index(
        "ix_conversation_evidence_records_conversation_id",
        "conversation_evidence_records",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversation_evidence_records_customer_id",
        "conversation_evidence_records",
        ["verified_customer_id"],
    )
    op.create_index(
        "ix_conversation_evidence_records_expires_at",
        "conversation_evidence_records",
        ["expires_at"],
    )
    op.create_index(
        "ix_conversation_evidence_records_created_at",
        "conversation_evidence_records",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_evidence_records_created_at",
        table_name="conversation_evidence_records",
    )
    op.drop_index(
        "ix_conversation_evidence_records_expires_at",
        table_name="conversation_evidence_records",
    )
    op.drop_index(
        "ix_conversation_evidence_records_customer_id",
        table_name="conversation_evidence_records",
    )
    op.drop_index(
        "ix_conversation_evidence_records_conversation_id",
        table_name="conversation_evidence_records",
    )
    op.drop_table("conversation_evidence_records")
