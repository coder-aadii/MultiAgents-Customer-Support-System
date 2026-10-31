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

        # Math / logical comparisons with < and > are preserved without corruption
        math_content = "5 < 10 and 20 > 15 comparison"
        resp_math = await client.post(
            f"/api/v1/widget/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json={"content": math_content, "client_message_id": str(uuid.uuid4())},
        )
        assert resp_math.status_code == 201
        assert resp_math.json()["content"] == math_content


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


# ==============================================================================
# Sub-phase 2.1 — Polling and Reconciliation Contract Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_polling_canonical_widget_endpoints_and_profile(setup_conversation_env):
    """Test canonical visitor widget endpoints: /conversation, /messages, /profile."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create visitor session
        sess_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        assert sess_resp.status_code == 200
        v_data = sess_resp.json()
        v_token = v_data["session_token"]
        v_conv_id = v_data["conversation_id"]

        # 2. GET /widget/conversation
        conv_resp = await client.get(
            "/api/v1/widget/conversation",
            headers={"Authorization": f"Bearer {v_token}"},
        )
        assert conv_resp.status_code == 200
        assert conv_resp.json()["id"] == v_conv_id
        assert conv_resp.json()["status"] == "open"

        # 3. POST /widget/messages (idempotent write)
        client_msg_id = str(uuid.uuid4())
        msg_post_resp = await client.post(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json={
                "content": "Hello from canonical widget endpoint",
                "client_message_id": client_msg_id,
            },
        )
        assert msg_post_resp.status_code == 201
        msg = msg_post_resp.json()
        assert msg["content"] == "Hello from canonical widget endpoint"
        assert msg["sender_type"] == "visitor"

        # 4. GET /widget/messages (polling)
        msgs_resp = await client.get(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {v_token}"},
        )
        assert msgs_resp.status_code == 200
        items = msgs_resp.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == msg["id"]
        assert msgs_resp.json()["next_cursor"] is not None

        # 5. PATCH /widget/profile
        profile_resp = await client.patch(
            "/api/v1/widget/profile",
            headers={"Authorization": f"Bearer {v_token}"},
            json={"name": "Jane Visitor", "email": "jane@example.com"},
        )
        assert profile_resp.status_code == 200
        profile_data = profile_resp.json()
        assert profile_data["name"] == "Jane Visitor"
        assert profile_data["email"] == "jane@example.com"


@pytest.mark.asyncio
async def test_polling_reconnect_recovery(setup_conversation_env):
    """Test client reconnect recovery: refetches server data and recovers state."""
    env = setup_conversation_env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create session and initial message
        sess_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        v_token = sess_resp.json()["session_token"]
        conv_id = sess_resp.json()["conversation_id"]

        # Visitor posts message
        await client.post(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json={"content": "Message before disconnect", "client_message_id": str(uuid.uuid4())},
        )

        # Staff claims and replies while visitor was "disconnected"
        await client.post(
            f"/api/v1/conversations/{conv_id}/claim",
            headers={"Authorization": f"Bearer {env['staff_token']}"},
        )
        await client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {env['staff_token']}"},
            json={
                "content": "Staff reply during disconnect",
                "client_message_id": str(uuid.uuid4()),
            },
        )

        # Visitor reconnects / recovers session with bearer token
        resume_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "Origin": "http://localhost:5174",
                "Authorization": f"Bearer {v_token}",
            },
        )
        assert resume_resp.status_code == 200
        assert resume_resp.json()["conversation_id"] == conv_id

        # Visitor refetches conversation detail and all messages
        detail = await client.get(
            "/api/v1/widget/conversation",
            headers={"Authorization": f"Bearer {v_token}"},
        )
        assert detail.status_code == 200
        assert detail.json()["status"] == "staff_active"

        messages_list = await client.get(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {v_token}"},
        )
        assert messages_list.status_code == 200
        items = messages_list.json()["items"]
        assert len(items) == 2
        assert items[0]["sender_type"] == "visitor"
        assert items[1]["sender_type"] == "staff"
        assert items[1]["content"] == "Staff reply during disconnect"


@pytest.mark.asyncio
async def test_polling_missed_message_backfill(setup_conversation_env):
    """Test polling catch-up: polling with last-seen cursor backfills all missed messages in order."""
    env = setup_conversation_env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        sess_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        v_token = sess_resp.json()["session_token"]
        conv_id = sess_resp.json()["conversation_id"]

        # Message 1
        msg1_res = await client.post(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json={"content": "First message", "client_message_id": str(uuid.uuid4())},
        )
        assert msg1_res.status_code == 201

        # Visitor polls and stores cursor after Message 1
        poll1 = await client.get(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {v_token}"},
        )
        assert len(poll1.json()["items"]) == 1
        cursor_after_m1 = poll1.json()["next_cursor"]
        assert cursor_after_m1 is not None

        # Two messages arrive while visitor is polling-idle
        await client.post(
            f"/api/v1/conversations/{conv_id}/claim",
            headers={"Authorization": f"Bearer {env['staff_token']}"},
        )
        await client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {env['staff_token']}"},
            json={"content": "Backfill message 2", "client_message_id": str(uuid.uuid4())},
        )
        await client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {env['staff_token']}"},
            json={"content": "Backfill message 3", "client_message_id": str(uuid.uuid4())},
        )

        # Visitor polls with cursor_after_m1
        backfill_poll = await client.get(
            f"/api/v1/widget/messages?cursor={cursor_after_m1}",
            headers={"Authorization": f"Bearer {v_token}"},
        )
        assert backfill_poll.status_code == 200
        new_items = backfill_poll.json()["items"]
        assert len(new_items) == 2
        assert new_items[0]["content"] == "Backfill message 2"
        assert new_items[1]["content"] == "Backfill message 3"

        # Cursor advances
        cursor_after_m3 = backfill_poll.json()["next_cursor"]
        assert cursor_after_m3 is not None
        assert cursor_after_m3 != cursor_after_m1

        # Next poll returns empty items
        idle_poll = await client.get(
            f"/api/v1/widget/messages?cursor={cursor_after_m3}",
            headers={"Authorization": f"Bearer {v_token}"},
        )
        assert idle_poll.status_code == 200
        assert len(idle_poll.json()["items"]) == 0


@pytest.mark.asyncio
async def test_polling_duplicate_http_responses(setup_conversation_env):
    """Test duplicate HTTP response handling: repeated client_message_id returns original without duplicates."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        sess_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        v_token = sess_resp.json()["session_token"]

        c_msg_id = str(uuid.uuid4())
        payload = {"content": "Idempotent send test", "client_message_id": c_msg_id}

        # Send 1
        res1 = await client.post(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json=payload,
        )
        assert res1.status_code == 201
        m1 = res1.json()

        # Send 2 (duplicate HTTP retry)
        res2 = await client.post(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {v_token}"},
            json=payload,
        )
        assert res2.status_code in (200, 201)
        m2 = res2.json()

        assert m1["id"] == m2["id"]
        assert m1["created_at"] == m2["created_at"]
        assert m1["client_message_id"] == c_msg_id

        # Verify polling list has exactly 1 message
        list_res = await client.get(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {v_token}"},
        )
        assert len(list_res.json()["items"]) == 1


@pytest.mark.asyncio
async def test_polling_no_cross_conversation_authorization(setup_conversation_env):
    """Test strict authorization boundaries: visitor A cannot discover or poll visitor B's data."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create session A
        sess_a = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        token_a = sess_a.json()["session_token"]

        # Create session B
        sess_b = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        token_b = sess_b.json()["session_token"]
        conv_b_id = sess_b.json()["conversation_id"]

        # Visitor B posts a private message
        await client.post(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"content": "Visitor B secret message", "client_message_id": str(uuid.uuid4())},
        )

        # Visitor A attempts to read Visitor B's conversation -> 404 (non-discoverable)
        cross_conv = await client.get(
            f"/api/v1/widget/conversations/{conv_b_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert cross_conv.status_code == 404

        # Visitor A attempts to poll Visitor B's messages -> 404
        cross_msgs = await client.get(
            f"/api/v1/widget/conversations/{conv_b_id}/messages",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert cross_msgs.status_code == 404

        # Visitor A calling canonical /widget/messages gets only A's messages (0 items)
        a_msgs = await client.get(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert a_msgs.status_code == 200
        assert len(a_msgs.json()["items"]) == 0

        # Unauthenticated poll -> 401
        unauth = await client.get("/api/v1/widget/messages")
        assert unauth.status_code == 401


@pytest.mark.asyncio
async def test_polling_no_broker_endpoints():
    """Verify no push/broker endpoints exist; REST is the authoritative transport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test endpoints that do not exist in v1.1 REST architecture
        res_ws = await client.get("/api/v1/ws")
        assert res_ws.status_code == 404

        res_events = await client.get("/api/v1/events")
        assert res_events.status_code == 404

        res_sub = await client.get("/api/v1/subscribe")
        assert res_sub.status_code == 404
