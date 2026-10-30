"""Tests for Alembic migrations and database schema."""

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic import command
from alembic.config import Config
from app.config import settings


def get_sync_engine():
    # Convert async psycopg URL if needed or use directly
    url = settings.DATABASE_URL
    return create_engine(url)


@pytest.fixture(scope="module")
def alembic_cfg():
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    return cfg


def test_migration_lifecycle(alembic_cfg):
    """Test full upgrade to head, verify objects, downgrade to phase 0, and re-upgrade."""
    engine = get_sync_engine()

    # 1. Upgrade to head
    command.upgrade(alembic_cfg, "head")

    # 2. Inspect created schema
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    expected_tables = {
        "users",
        "refresh_tokens",
        "widget_settings",
        "visitors",
        "visitor_sessions",
        "conversations",
        "messages",
        "handoffs",
        "audit_events",
    }
    for expected in expected_tables:
        assert expected in tables, f"Expected table '{expected}' not found in {tables}"

    # Verify extensions are present
    with engine.connect() as conn:
        exts = {
            row[0]
            for row in conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('pgcrypto', 'vector')")
            ).fetchall()
        }
        assert "pgcrypto" in exts
        assert "vector" in exts

        # Verify enums exist
        enums = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN ('user_role', 'conversation_status', 'sender_type', 'handoff_reason', 'handoff_status', 'actor_type')"
                )
            ).fetchall()
        }
        assert "user_role" in enums
        assert "conversation_status" in enums
        assert "sender_type" in enums
        assert "handoff_reason" in enums
        assert "handoff_status" in enums
        assert "actor_type" in enums

        # Verify widget_settings singleton seed
        settings_row = conn.execute(
            text(
                "SELECT allowed_origin, welcome_message, accent_color FROM widget_settings LIMIT 1"
            )
        ).fetchone()
        assert settings_row is not None
        assert settings_row[0] == "http://localhost:5174"
        assert settings_row[1] == "Hi! How can MCSS Support help today?"
        assert settings_row[2] == "#2563eb"

    # Verify foreign keys and unique constraints
    users_fks = inspector.get_foreign_keys("users")
    assert len(users_fks) == 0

    refresh_fks = {
        fk["name"]: fk["referred_table"] for fk in inspector.get_foreign_keys("refresh_tokens")
    }
    assert refresh_fks.get("fk_refresh_tokens_user_id") == "users"

    conv_fks = {
        fk["name"]: fk["referred_table"] for fk in inspector.get_foreign_keys("conversations")
    }
    assert conv_fks.get("fk_conversations_visitor_id") == "visitors"

    msg_fks = {fk["name"]: fk["referred_table"] for fk in inspector.get_foreign_keys("messages")}
    assert msg_fks.get("fk_messages_conversation_id") == "conversations"

    handoff_fks = {
        fk["name"]: fk["referred_table"] for fk in inspector.get_foreign_keys("handoffs")
    }
    assert handoff_fks.get("fk_handoffs_conversation_id") == "conversations"

    # 3. Downgrade to Phase 0 extensions (reverses Phase 1)
    command.downgrade(alembic_cfg, "0001_phase_0_extensions")

    inspector_after_downgrade = inspect(engine)
    downgraded_tables = set(inspector_after_downgrade.get_table_names())
    for expected in expected_tables:
        assert (
            expected not in downgraded_tables
        ), f"Table '{expected}' should be dropped after downgrade"

    # Verify Phase 0 extensions were NOT dropped
    with engine.connect() as conn:
        exts_after = {
            row[0]
            for row in conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('pgcrypto', 'vector')")
            ).fetchall()
        }
        assert "pgcrypto" in exts_after
        assert "vector" in exts_after

    # 4. Re-upgrade to head
    command.upgrade(alembic_cfg, "head")

    inspector_final = inspect(engine)
    final_tables = set(inspector_final.get_table_names())
    for expected in expected_tables:
        assert expected in final_tables, f"Table '{expected}' not restored after re-upgrade"
