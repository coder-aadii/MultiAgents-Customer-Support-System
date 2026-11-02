"""Phase 5D Multi-tenant organizations and memberships.

Revision ID: 0009_multi_tenant_organizations
Revises: 0008_controlled_actions
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_multi_tenant_organizations"
down_revision: str | None = "0008_controlled_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    # 1. organizations
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sso_config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("settings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])
    op.create_index("ix_organizations_is_active", "organizations", ["is_active"])

    # 2. organization_memberships
    op.create_table(
        "organization_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE", name="fk_org_memberships_org_id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_org_memberships_user_id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False, server_default="agent"),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_membership_org_user"),
    )
    op.create_index("ix_org_memberships_org_id", "organization_memberships", ["organization_id"])
    op.create_index("ix_org_memberships_user_id", "organization_memberships", ["user_id"])
    op.create_index("ix_org_memberships_role", "organization_memberships", ["role"])

    # 3. Seed default organization
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO organizations (id, slug, name, is_active, settings_json, created_at, updated_at) "
            "VALUES (:id, 'default', 'Default Organization', true, '{}'::jsonb, now(), now()) "
            "ON CONFLICT (slug) DO NOTHING;"
        ),
        {"id": DEFAULT_ORG_ID},
    )

    # 4. Backfill existing users into default organization memberships
    conn.execute(
        sa.text(
            "INSERT INTO organization_memberships (id, organization_id, user_id, role, permissions, created_at) "
            "SELECT gen_random_uuid(), CAST(:org_id AS uuid), id, "
            "CASE WHEN role = 'admin' THEN 'owner' ELSE 'agent' END, CAST('[]' AS jsonb), now() "
            "FROM users "
            "ON CONFLICT (organization_id, user_id) DO NOTHING;"
        ),
        {"org_id": DEFAULT_ORG_ID},
    )


def downgrade() -> None:
    op.drop_index("ix_org_memberships_role", table_name="organization_memberships")
    op.drop_index("ix_org_memberships_user_id", table_name="organization_memberships")
    op.drop_index("ix_org_memberships_org_id", table_name="organization_memberships")
    op.drop_table("organization_memberships")

    op.drop_index("ix_organizations_is_active", table_name="organizations")
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_table("organizations")
