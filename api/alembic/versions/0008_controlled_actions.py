"""Phase 5C Controlled business actions, idempotency records, and approval queues.

Revision ID: 0008_controlled_actions
Revises: 0007_enterprise_connectors
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_controlled_actions"
down_revision: str | None = "0007_enterprise_connectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. business_actions
    op.create_table(
        "business_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE", name="fk_business_actions_conversation_id"),
            nullable=False,
        ),
        sa.Column("customer_id", sa.String(128), nullable=False),
        sa.Column("action_name", sa.String(64), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="drafted"),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("summary_message", sa.Text(), nullable=False),
        sa.Column("required_confirmation_phrase", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_business_actions_reviewed_by_user_id"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("execution_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_business_actions_idempotency_key"),
    )
    op.create_index("ix_business_actions_conversation_id", "business_actions", ["conversation_id"])
    op.create_index("ix_business_actions_customer_id", "business_actions", ["customer_id"])
    op.create_index("ix_business_actions_action_name", "business_actions", ["action_name"])
    op.create_index("ix_business_actions_status", "business_actions", ["status"])
    op.create_index("ix_business_actions_risk_level", "business_actions", ["risk_level"])
    op.create_index("ix_business_actions_expires_at", "business_actions", ["expires_at"])
    op.create_index("ix_business_actions_reviewed_by_user_id", "business_actions", ["reviewed_by_user_id"])

    # 2. action_audit_journal
    op.create_table(
        "action_audit_journal",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("business_actions.id", ondelete="CASCADE", name="fk_action_audit_journal_action_id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_action_audit_journal_action_id", "action_audit_journal", ["action_id"])
    op.create_index("ix_action_audit_journal_event_type", "action_audit_journal", ["event_type"])
    op.create_index("ix_action_audit_journal_created_at", "action_audit_journal", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_action_audit_journal_created_at", table_name="action_audit_journal")
    op.drop_index("ix_action_audit_journal_event_type", table_name="action_audit_journal")
    op.drop_index("ix_action_audit_journal_action_id", table_name="action_audit_journal")
    op.drop_table("action_audit_journal")

    op.drop_index("ix_business_actions_reviewed_by_user_id", table_name="business_actions")
    op.drop_index("ix_business_actions_expires_at", table_name="business_actions")
    op.drop_index("ix_business_actions_risk_level", table_name="business_actions")
    op.drop_index("ix_business_actions_status", table_name="business_actions")
    op.drop_index("ix_business_actions_action_name", table_name="business_actions")
    op.drop_index("ix_business_actions_customer_id", table_name="business_actions")
    op.drop_index("ix_business_actions_conversation_id", table_name="business_actions")
    op.drop_table("business_actions")

