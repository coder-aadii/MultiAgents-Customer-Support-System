"""Phase 5D Compound organization unique indexes.

Revision ID: 0011_vector_and_unique_indexes
Revises: 0010_tenant_scoping_columns
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_vector_and_unique_indexes"
down_revision: str | None = "0010_tenant_scoping_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop global unique email constraint on users
    op.drop_index("ix_users_email", table_name="users")
    op.drop_constraint("uq_users_email", "users", type_="unique")

    # 2. Add compound (organization_id, email) unique constraint
    op.create_unique_constraint(
        "uq_users_organization_id_email", "users", ["organization_id", "email"]
    )
    op.create_index(
        "ix_users_organization_id_email", "users", ["organization_id", "email"]
    )


def downgrade() -> None:
    op.drop_index("ix_users_organization_id_email", table_name="users")
    op.drop_constraint("uq_users_organization_id_email", "users", type_="unique")

    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

