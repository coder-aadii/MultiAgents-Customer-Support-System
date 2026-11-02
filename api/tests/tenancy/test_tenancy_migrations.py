from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

from app.config import settings

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def get_sync_engine():
    return create_engine(settings.DATABASE_URL)


def test_default_organization_backfill_succeeds():
    """Verify that default organization exists and has default slug and name."""
    engine = get_sync_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, slug, name, is_active FROM organizations WHERE id = :id"),
            {"id": str(DEFAULT_ORG_ID)},
        ).fetchone()

        assert row is not None
        assert str(row[0]) == str(DEFAULT_ORG_ID)
        assert row[1] == "default"
        assert row[2] == "Default Organization"
        assert row[3] is True


def test_existing_conversations_bound_to_default_org():
    """Verify all tenant tables have organization_id column populated."""
    engine = get_sync_engine()
    with engine.connect() as conn:
        for table in ["users", "conversations", "messages", "visitors", "knowledge_documents", "business_actions"]:
            count_unscoped = conn.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE organization_id IS NULL")
            ).scalar()
            assert count_unscoped == 0, f"Table {table} has unscoped rows!"


def test_organization_membership_foreign_keys():
    """Verify organization_memberships has valid foreign keys and indexes."""
    engine = get_sync_engine()
    inspector = inspect(engine)

    fks = inspector.get_foreign_keys("organization_memberships")
    fk_tables = {fk["referred_table"] for fk in fks}
    assert "organizations" in fk_tables
    assert "users" in fk_tables

    uniques = inspector.get_unique_constraints("organization_memberships")
    unique_cols = [set(u["column_names"]) for u in uniques]
    assert {"organization_id", "user_id"} in unique_cols
