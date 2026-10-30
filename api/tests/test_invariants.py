"""Tests for Sub-phase 1.2 Relational model and database invariants."""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import settings
from app.schemas.user import UserBase, UserCreate


def get_sync_engine():
    return create_engine(settings.DATABASE_URL)


def test_user_email_normalization_schema():
    """Verify email normalization (strip and lowercase) in user schemas."""
    user = UserBase(email="  Admin.User@Example.COM  ", display_name="Admin")
    assert user.email == "admin.user@example.com"

    create_user = UserCreate(
        email="  Staff@Example.COM  ", display_name="Staff", password="password123"
    )
    assert create_user.email == "staff@example.com"


def test_audit_events_immutable_cannot_update_or_delete():
    """Verify audit_events cannot be updated or deleted due to append-only constraint."""
    engine = get_sync_engine()
    event_id1 = uuid.uuid4()
    event_id2 = uuid.uuid4()

    # 1. Insert first audit event in committed transaction
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_events (id, actor_type, event_type, resource_type, metadata, created_at) "
                "VALUES (:id, 'system', 'test_event1', 'system', '{}'::jsonb, now())"
            ),
            {"id": event_id1},
        )

    # 2. Attempt UPDATE on event_id1 - must fail with exception from trigger
    with pytest.raises(DBAPIError) as exc_update:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE audit_events SET event_type = 'tampered' WHERE id = :id"),
                {"id": event_id1},
            )
    assert "audit_events is append-only" in str(exc_update.value)

    # 3. Insert second audit event in committed transaction
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_events (id, actor_type, event_type, resource_type, metadata, created_at) "
                "VALUES (:id, 'system', 'test_event2', 'system', '{}'::jsonb, now())"
            ),
            {"id": event_id2},
        )

    # 4. Attempt DELETE on event_id2 - must fail with exception from trigger
    with pytest.raises(DBAPIError) as exc_delete:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM audit_events WHERE id = :id"),
                {"id": event_id2},
            )
    assert "audit_events is append-only" in str(exc_delete.value)


def test_message_client_message_id_idempotency_constraint():
    """Verify uniqueness constraint on (conversation_id, client_message_id)."""
    engine = get_sync_engine()
    visitor_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    client_msg_id = uuid.uuid4()

    with engine.begin() as conn:
        # Create visitor and conversation
        conn.execute(
            text(
                "INSERT INTO visitors (id, name, created_at, last_seen_at) VALUES (:vid, 'Visitor', now(), now())"
            ),
            {"vid": visitor_id},
        )
        conn.execute(
            text(
                "INSERT INTO conversations (id, visitor_id, status, last_message_at, created_at, updated_at) "
                "VALUES (:cid, :vid, 'open', now(), now(), now())"
            ),
            {"cid": conv_id, "vid": visitor_id},
        )

        # Insert first message with client_message_id
        conn.execute(
            text(
                "INSERT INTO messages (id, conversation_id, client_message_id, sender_type, content, metadata, created_at) "
                "VALUES (:mid, :cid, :cmid, 'visitor', 'Hello', '{}'::jsonb, now())"
            ),
            {"mid": uuid.uuid4(), "cid": conv_id, "cmid": client_msg_id},
        )

        # Duplicate insert with same (conversation_id, client_message_id) must fail
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO messages (id, conversation_id, client_message_id, sender_type, content, metadata, created_at) "
                    "VALUES (:mid2, :cid, :cmid, 'visitor', 'Hello Again', '{}'::jsonb, now())"
                ),
                {"mid2": uuid.uuid4(), "cid": conv_id, "cmid": client_msg_id},
            )


def test_handoff_one_per_conversation_constraint():
    """Verify unique constraint on handoffs.conversation_id (max 1 handoff per conversation)."""
    engine = get_sync_engine()
    visitor_id = uuid.uuid4()
    conv_id = uuid.uuid4()

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO visitors (id, created_at, last_seen_at) VALUES (:vid, now(), now())"),
            {"vid": visitor_id},
        )
        conn.execute(
            text(
                "INSERT INTO conversations (id, visitor_id, status, last_message_at, created_at, updated_at) "
                "VALUES (:cid, :vid, 'open', now(), now(), now())"
            ),
            {"cid": conv_id, "vid": visitor_id},
        )

        # First handoff succeeds
        conn.execute(
            text(
                "INSERT INTO handoffs (id, conversation_id, reason, summary, status, created_at) "
                "VALUES (:hid, :cid, 'visitor_requested', 'Help needed', 'queued', now())"
            ),
            {"hid": uuid.uuid4(), "cid": conv_id},
        )

        # Second handoff for same conversation must fail
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO handoffs (id, conversation_id, reason, summary, status, created_at) "
                    "VALUES (:hid2, :cid, 'low_confidence', 'Another handoff', 'queued', now())"
                ),
                {"hid2": uuid.uuid4(), "cid": conv_id},
            )
