"""Tests for Sub-phase 1.6 Conversations, messages, and handoffs."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import async_session_factory
from app.main import app
from app.models.handoff import Handoff, HandoffReason, HandoffStatus
from app.models.user import User, UserRole
from app.security.password import hash_password
from app.security.tokens import create_access_jwt


@pytest.fixture
async def setup_conversation_env():
    """Setup staff user, admin user, visitor, and test conversation."""
    async with async_session_factory() as session:
        staff_id = uuid.uuid4()
        staff = User(
            id=staff_id,
            email=f"conv_staff_{staff_id.hex[:6]}@example.com",
            password_hash=hash_password("staffpassword123"),
            display_name="Staff Operator",
            role=UserRole.staff,
            is_active=True,
        )
        session.add(staff)

        other_staff_id = uuid.uuid4()
        other_staff = User(
            id=other_staff_id,
            email=f"other_staff_{other_staff_id.hex[:6]}@example.com",
            password_hash=hash_password("staffpassword123"),
            display_name="Other Staff",
            role=UserRole.staff,
            is_active=True,
        )
        session.add(other_staff)
        await session.commit()

        staff_token = create_access_jwt(str(staff.id), "staff")
        other_staff_token = create_access_jwt(str(other_staff.id), "staff")

        return {
            "staff": staff,
            "staff_token": staff_token,
            "other_staff": other_staff,
            "other_staff_token": other_staff_token,
        }


@pytest.mark.asyncio
async def test_staff_list_and_detail_conversation(setup_conversation_env):
    """Test staff listing conversations and retrieving conversation detail."""
    env = setup_conversation_env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a visitor session first
        sess_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        assert sess_resp.status_code == 200
        conv_id = sess_resp.json()["conversation_id"]

        # Staff lists conversations
        list_resp = await client.get(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {env['staff_token']}"},
        )
        assert list_resp.status_code == 200
        conv_list = list_resp.json()
        assert any(c["id"] == conv_id for c in conv_list["items"])

        # Staff retrieves detail
        detail_resp = await client.get(
            f"/api/v1/conversations/{conv_id}",
            headers={"Authorization": f"Bearer {env['staff_token']}"},
        )
        assert detail_resp.status_code == 200
        assert detail_resp.json()["id"] == conv_id
        assert detail_resp.json()["status"] == "open"


@pytest.mark.asyncio
async def test_idempotent_message_posting(setup_conversation_env):
    """Test client_message_id ensures idempotent message posting without duplicates."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create visitor session
        sess_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        conv_id = sess_resp.json()["conversation_id"]
        v_token = sess_resp.json()["session_token"]

        client_msg_id = str(uuid.uuid4())
        payload = {"content": "Hello, need help!", "client_message_id": client_msg_id}

        # 2. First send from visitor
        resp1 = await client.post(
            f"/api/v1/widget/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json=payload,
        )
        assert resp1.status_code == 201
        msg1 = resp1.json()
        assert msg1["content"] == "Hello, need help!"
        assert msg1["sender_type"] == "visitor"

        # 3. Retry send with same client_message_id (idempotent replay)
        resp2 = await client.post(
            f"/api/v1/widget/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json=payload,
        )
        assert resp2.status_code in (200, 201)
        msg2 = resp2.json()
        assert msg2["id"] == msg1["id"]
        assert msg2["content"] == msg1["content"]

        # 4. Verify message list only contains 1 message
        msgs_resp = await client.get(
            f"/api/v1/widget/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {v_token}"},
        )
        assert msgs_resp.status_code == 200
        assert len(msgs_resp.json()["items"]) == 1


@pytest.mark.asyncio
async def test_message_validation_trimming_and_html_rejection(setup_conversation_env):
    """Test whitespace trimming and rejection of empty/html-only messages."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        sess_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        conv_id = sess_resp.json()["conversation_id"]
        v_token = sess_resp.json()["session_token"]

        # Empty after trim
        resp_empty = await client.post(
            f"/api/v1/widget/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json={"content": "   ", "client_message_id": str(uuid.uuid4())},
        )
        assert resp_empty.status_code == 422

        # HTML only stripped to empty
        resp_html = await client.post(
            f"/api/v1/widget/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json={"content": "<div><span> </span></div>", "client_message_id": str(uuid.uuid4())},
        )
        assert resp_html.status_code == 422

        # Mixed HTML is sanitized
        resp_mixed = await client.post(
            f"/api/v1/widget/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json={"content": "<b>Important</b> text", "client_message_id": str(uuid.uuid4())},
        )
        assert resp_mixed.status_code == 201
        assert resp_mixed.json()["content"] == "Important text"


@pytest.mark.asyncio
async def test_staff_takeover_and_close(setup_conversation_env):
    """Test staff claiming and closing conversation, rejecting messages after close with 409."""
    env = setup_conversation_env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        sess_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        conv_id = sess_resp.json()["conversation_id"]
        v_token = sess_resp.json()["session_token"]

        # 1. Staff 1 claims conversation
        claim1 = await client.post(
            f"/api/v1/conversations/{conv_id}/claim",
            headers={"Authorization": f"Bearer {env['staff_token']}"},
        )
        assert claim1.status_code == 200
        assert claim1.json()["status"] == "staff_active"
        assert claim1.json()["assigned_user_id"] == str(env["staff"].id)

        # 2. Staff 1 claims again (idempotent)
        claim1_repeat = await client.post(
            f"/api/v1/conversations/{conv_id}/claim",
            headers={"Authorization": f"Bearer {env['staff_token']}"},
        )
        assert claim1_repeat.status_code == 200

        # 3. Staff 2 takes over (conflict-safe takeover)
        claim2 = await client.post(
            f"/api/v1/conversations/{conv_id}/claim",
            headers={"Authorization": f"Bearer {env['other_staff_token']}"},
        )
        assert claim2.status_code == 200
        assert claim2.json()["assigned_user_id"] == str(env["other_staff"].id)

        # 4. Staff sends message
        msg_resp = await client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {env['other_staff_token']}"},
            json={"content": "I am handling your inquiry.", "client_message_id": str(uuid.uuid4())},
        )
        assert msg_resp.status_code == 201
        assert msg_resp.json()["sender_type"] == "staff"

        # 5. Staff closes conversation
        close_resp = await client.post(
            f"/api/v1/conversations/{conv_id}/close",
            headers={"Authorization": f"Bearer {env['other_staff_token']}"},
        )
        assert close_resp.status_code == 200
        assert close_resp.json()["status"] == "closed"

        # 6. Subsequent messages from visitor return 409 CONVERSATION_CLOSED
        v_post_closed = await client.post(
            f"/api/v1/widget/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json={"content": "Can I still ask?", "client_message_id": str(uuid.uuid4())},
        )
        assert v_post_closed.status_code == 409
        assert v_post_closed.json()["error"]["code"] == "CONVERSATION_CLOSED"

        # 7. Subsequent messages from staff return 409 CONVERSATION_CLOSED
        s_post_closed = await client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {env['other_staff_token']}"},
            json={"content": "One more note", "client_message_id": str(uuid.uuid4())},
        )
        assert s_post_closed.status_code == 409
        assert s_post_closed.json()["error"]["code"] == "CONVERSATION_CLOSED"


@pytest.mark.asyncio
async def test_handoff_lifecycle(setup_conversation_env):
    """Test listing and claiming queued handoffs."""
    env = setup_conversation_env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create conversation and handoff in DB
        sess_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        conv_id = sess_resp.json()["conversation_id"]

        handoff_id = uuid.uuid4()
        async with async_session_factory() as session:
            h = Handoff(
                id=handoff_id,
                conversation_id=uuid.UUID(conv_id),
                reason=HandoffReason.visitor_requested,
                summary="Visitor requested live agent",
                status=HandoffStatus.queued,
            )
            session.add(h)
            await session.commit()

        # Staff lists queued handoffs
        list_h = await client.get(
            "/api/v1/handoffs?status=queued",
            headers={"Authorization": f"Bearer {env['staff_token']}"},
        )
        assert list_h.status_code == 200
        assert any(item["id"] == str(handoff_id) for item in list_h.json()["items"])

        # Staff claims handoff
        claim_h = await client.post(
            f"/api/v1/handoffs/{handoff_id}/claim",
            headers={"Authorization": f"Bearer {env['staff_token']}"},
        )
        assert claim_h.status_code == 200
        assert claim_h.json()["status"] == "claimed"
        assert claim_h.json()["claimed_by_user_id"] == str(env["staff"].id)
