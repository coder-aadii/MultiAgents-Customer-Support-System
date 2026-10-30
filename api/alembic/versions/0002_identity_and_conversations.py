"""Phase 1 identity and conversations schema.

Revision ID: 0002_identity_and_conversations
Revises: 0001_phase_0_extensions
Create Date: 2026-10-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_identity_and_conversations"
down_revision: Union[str, None] = "0001_phase_0_extensions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Enums
    user_role = postgresql.ENUM("admin", "staff", name="user_role", create_type=False)
    user_role.create(op.get_bind(), checkfirst=True)

    conversation_status = postgresql.ENUM(
        "open", "waiting_for_staff", "staff_active", "closed", name="conversation_status", create_type=False
    )
    conversation_status.create(op.get_bind(), checkfirst=True)

    sender_type = postgresql.ENUM("visitor", "assistant", "staff", "system", name="sender_type", create_type=False)
    sender_type.create(op.get_bind(), checkfirst=True)

    handoff_reason = postgresql.ENUM(
        "visitor_requested",
        "low_confidence",
        "unsupported_request",
        "policy",
        "provider_failure",
        name="handoff_reason",
        create_type=False,
    )
    handoff_reason.create(op.get_bind(), checkfirst=True)

    handoff_status = postgresql.ENUM("queued", "claimed", "resolved", name="handoff_status", create_type=False)
    handoff_status.create(op.get_bind(), checkfirst=True)

    actor_type = postgresql.ENUM("user", "visitor", "system", name="actor_type", create_type=False)
    actor_type.create(op.get_bind(), checkfirst=True)

    # 2. Users table
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # 3. Refresh tokens table
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_refresh_tokens_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"], ["refresh_tokens.id"], name="fk_refresh_tokens_replaced_by_id", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False)
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"], unique=False)
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"], unique=False)

    # 4. Widget settings table
    op.create_table(
        "widget_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("allowed_origin", sa.String(length=2048), nullable=False),
        sa.Column("welcome_message", sa.Text(), nullable=False),
        sa.Column("accent_color", sa.String(length=7), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_widget_settings"),
    )

    # 5. Visitors table
    op.create_table(
        "visitors",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_visitors"),
    )

    # 6. Conversations table
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("visitor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            conversation_status,
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("assigned_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["visitor_id"], ["visitors.id"], name="fk_conversations_visitor_id"),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["users.id"], name="fk_conversations_assigned_user_id"),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
    )
    op.create_index(
        "ix_conversations_status_last_message",
        "conversations",
        ["status", sa.text("last_message_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_conversations_visitor_created",
        "conversations",
        ["visitor_id", sa.text("created_at DESC")],
        unique=False,
    )

    # 7. Visitor sessions table
    op.create_table(
        "visitor_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("visitor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=2048), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["visitor_id"], ["visitors.id"], name="fk_visitor_sessions_visitor_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_visitor_sessions_conversation_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_visitor_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_visitor_sessions_token_hash"),
    )
    op.create_index("ix_visitor_sessions_token_hash", "visitor_sessions", ["token_hash"], unique=True)
    op.create_index("ix_visitor_sessions_visitor_id", "visitor_sessions", ["visitor_id"], unique=False)
    op.create_index("ix_visitor_sessions_conversation_id", "visitor_sessions", ["conversation_id"], unique=False)
    op.create_index("ix_visitor_sessions_expires_at", "visitor_sessions", ["expires_at"], unique=False)

    # 8. Messages table
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "sender_type",
            sender_type,
            nullable=False,
        ),
        sa.Column("sender_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name="fk_messages_conversation_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], name="fk_messages_sender_user_id"),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.UniqueConstraint("conversation_id", "client_message_id", name="uq_messages_conversation_client_message_id"),
    )
    op.create_index("ix_messages_conversation_created", "messages", ["conversation_id", "created_at"], unique=False)

    # 9. Handoffs table
    op.create_table(
        "handoffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "reason",
            handoff_reason,
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "status",
            handoff_status,
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("claimed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name="fk_handoffs_conversation_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["claimed_by_user_id"], ["users.id"], name="fk_handoffs_claimed_by_user_id"),
        sa.PrimaryKeyConstraint("id", name="pk_handoffs"),
        sa.UniqueConstraint("conversation_id", name="uq_handoffs_conversation_id"),
    )
    op.create_index("ix_handoffs_status_created", "handoffs", ["status", "created_at"], unique=False)

    # 10. Audit events table
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("actor_type", actor_type, nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index(
        "ix_audit_events_created_at",
        "audit_events",
        [sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index("ix_audit_events_resource", "audit_events", ["resource_type", "resource_id"], unique=False)

    # Immutable audit_events constraint trigger
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_events_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only and cannot be updated or deleted';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_audit_events_immutable
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_events_mutation();
        """
    )

    # 11. Seed singleton widget_settings row
    op.execute(
        sa.text(
            "INSERT INTO widget_settings (id, allowed_origin, welcome_message, accent_color, updated_at) "
            "VALUES (gen_random_uuid(), 'http://localhost:5174', 'Hi! How can MCSS Support help today?', '#2563eb', now()) "
            "ON CONFLICT DO NOTHING"
        )
    )


def downgrade() -> None:
    # Drop trigger and function
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_events_mutation")

    # Drop tables in reverse dependency order
    op.drop_table("audit_events")
    op.drop_table("handoffs")
    op.drop_table("messages")
    op.drop_table("visitor_sessions")
    op.drop_table("conversations")
    op.drop_table("visitors")
    op.drop_table("widget_settings")
    op.drop_table("refresh_tokens")
    op.drop_table("users")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS actor_type")
    op.execute("DROP TYPE IF EXISTS handoff_status")
    op.execute("DROP TYPE IF EXISTS handoff_reason")
    op.execute("DROP TYPE IF EXISTS sender_type")
    op.execute("DROP TYPE IF EXISTS conversation_status")
    op.execute("DROP TYPE IF EXISTS user_role")
