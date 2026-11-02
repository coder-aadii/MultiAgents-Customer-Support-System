"""Phase 5F Support operations: teams, routing, internal notes, SLAs, and CSAT surveys.

Revision ID: 0013_support_operations
Revises: 0012_omnichannel_support
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_support_operations"
down_revision: str | None = "0012_omnichannel_support"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. support_teams
    op.create_table(
        "support_teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_support_teams_organization_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("routing_strategy", sa.String(32), nullable=False, server_default=sa.text("'least_busy'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_support_teams_org", "support_teams", ["organization_id"])

    # 2. team_memberships
    op.create_table(
        "team_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("support_teams.id", ondelete="CASCADE", name="fk_team_memberships_team_id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_team_memberships_user_id"),
            nullable=False,
        ),
        sa.Column("is_team_lead", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("max_concurrent_chats", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),
    )

    # 3. conversation_internal_notes
    op.create_table(
        "conversation_internal_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_conversation_notes_org_id"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE", name="fk_conversation_notes_conv_id"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_conversation_notes_author_id"),
            nullable=False,
        ),
        sa.Column("note_text", sa.Text(), nullable=False),
        sa.Column("is_whisper", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_conversation_notes_conv", "conversation_internal_notes", ["conversation_id"])

    # 4. sla_policies
    op.create_table(
        "sla_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_sla_policies_org_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False, server_default=sa.text("'medium'")),
        sa.Column("first_response_target_minutes", sa.Integer(), nullable=False),
        sa.Column("resolution_target_minutes", sa.Integer(), nullable=False),
        sa.Column("business_hours_schedule_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # 5. csat_surveys
    op.create_table(
        "csat_surveys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_csat_surveys_org_id"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE", name="fk_csat_surveys_conv_id"),
            unique=True,
            nullable=False,
        ),
        sa.Column("customer_id", sa.String(128), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # 6. Add columns to conversations
    op.add_column(
        "conversations",
        sa.Column(
            "assigned_team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("support_teams.id", ondelete="SET NULL", name="fk_conversations_assigned_team_id"),
            nullable=True,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "assigned_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_conversations_assigned_agent_id"),
            nullable=True,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column("priority", sa.String(16), nullable=False, server_default=sa.text("'medium'")),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "sla_policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sla_policies.id", ondelete="SET NULL", name="fk_conversations_sla_policy_id"),
            nullable=True,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("sla_breached", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("conversations", "sla_breached")
    op.drop_column("conversations", "resolved_at")
    op.drop_column("conversations", "first_response_at")
    op.drop_column("conversations", "sla_policy_id")
    op.drop_column("conversations", "priority")
    op.drop_column("conversations", "assigned_agent_id")
    op.drop_column("conversations", "assigned_team_id")
    op.drop_table("csat_surveys")
    op.drop_table("sla_policies")
    op.drop_table("conversation_internal_notes")
    op.drop_table("team_memberships")
    op.drop_table("support_teams")

