"""Phase 5E Omnichannel support: channel configurations, channel identities, and delivery tracking.

Revision ID: 0012_omnichannel_support
Revises: 0011_vector_and_unique_indexes
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_omnichannel_support"
down_revision: str | None = "0011_vector_and_unique_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. channel_configurations table
    op.create_table(
        "channel_configurations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_channel_configurations_organization_id"),
            nullable=False,
        ),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("webhook_secret_enc", sa.String(255), nullable=False),
        sa.Column("api_credentials_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("channel_metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "channel_type", name="uq_channel_configurations_org_type"),
    )
    op.create_index(
        "ix_channel_configurations_org_type",
        "channel_configurations",
        ["organization_id", "channel_type"],
    )

    # 2. channel_identities table
    op.create_table(
        "channel_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_channel_identities_organization_id"),
            nullable=False,
        ),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("external_user_id", sa.String(128), nullable=False),
        sa.Column("customer_id", sa.String(128), nullable=True),
        sa.Column(
            "visitor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("visitors.id", ondelete="CASCADE", name="fk_channel_identities_visitor_id"),
            nullable=False,
        ),
        sa.Column("profile_metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "channel_type", "external_user_id", name="uq_channel_identities_org_channel_user"),
    )
    op.create_index(
        "ix_channel_identities_lookup",
        "channel_identities",
        ["organization_id", "channel_type", "external_user_id"],
    )

    # 3. Add delivery tracking columns to messages table
    op.add_column(
        "messages",
        sa.Column("channel_type", sa.String(32), nullable=False, server_default=sa.text("'widget'")),
    )
    op.add_column(
        "messages",
        sa.Column("external_message_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("delivery_status", sa.String(32), nullable=False, server_default=sa.text("'delivered'")),
    )
    op.add_column(
        "messages",
        sa.Column("delivery_error_code", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "delivery_error_code")
    op.drop_column("messages", "delivery_status")
    op.drop_column("messages", "external_message_id")
    op.drop_column("messages", "channel_type")
    op.drop_table("channel_identities")
    op.drop_table("channel_configurations")

