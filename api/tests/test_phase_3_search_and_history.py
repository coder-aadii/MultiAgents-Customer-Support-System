"""Tests for Phase 3: Inbox Multi-Attribute Search (Item 1) & Customer Past History (Item 2).

Invariants:
1. Multi-Attribute Search (Item 1):
   - Filtering by q matches visitor name (case-insensitive substring/trigram).
   - Filtering by q matches visitor email.
   - Filtering by q matches exact conversation UUID or visitor UUID.
   - Filtering by q matches text content within messages of the conversation.
   - Filtering by customer_id filters strictly by visitor_id.
   - Strict tenant boundary: Org A search never returns Org B records.
2. In-Chat Customer Past Conversation History (Item 2):
   - GET /api/v1/conversations/{conv_id}/customer-history returns all other conversations
     belonging to the same visitor, ordered by created_at DESC.
   - Includes message count, last message preview snippet, and assigned agent name.
   - Strict tenant boundary: Cannot fetch history for conversation in another tenant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import async_session_factory
from app.main import app
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, SenderType
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.visitor import Visitor
from app.security.password import hash_password
from app.security.tokens import create_access_jwt
from app.tenancy.context import TenantContext


@pytest.fixture
async def setup_phase_3_env():
    """Setup tenants, users, visitors, conversations, and messages for Phase 3 testing."""
    async with async_session_factory() as session:
        # Organization A
        org_a = Organization(
            id=uuid.uuid4(),
            name="Org Alpha Phase 3",
            slug=f"org-alpha-p3-{uuid.uuid4().hex[:6]}",
        )
        session.add(org_a)

        # Organization B
        org_b = Organization(
            id=uuid.uuid4(),
            name="Org Beta Phase 3",
            slug=f"org-beta-p3-{uuid.uuid4().hex[:6]}",
        )
        session.add(org_b)
        await session.flush()

        # Staff User in Org A
        staff_a = User(
            id=uuid.uuid4(),
            organization_id=org_a.id,
            email=f"staff_a_{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("StaffPass123!"),
            display_name="Agent Alice",
            role=UserRole.staff,
            is_active=True,
        )
        session.add(staff_a)

        # Staff User in Org B
        staff_b = User(
            id=uuid.uuid4(),
            organization_id=org_b.id,
            email=f"staff_b_{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("StaffPass123!"),
            display_name="Agent Bob",
            role=UserRole.staff,
            is_active=True,
        )
        session.add(staff_b)
        await session.flush()

        # Visitors in Org A
        now = datetime.now(UTC)
        vis_1 = Visitor(
            id=uuid.uuid4(),
            organization_id=org_a.id,
            name="Sarah Connor",
            email="sarah.connor@cyberdyne.io",
            created_at=now - timedelta(days=5),
            last_seen_at=now,
        )
        vis_2 = Visitor(
            id=uuid.uuid4(),
            organization_id=org_a.id,
            name="John Doe",
            email="john.doe@example.com",
            created_at=now - timedelta(days=2),
            last_seen_at=now,
        )
        session.add_all([vis_1, vis_2])

        # Visitor in Org B
        vis_b = Visitor(
            id=uuid.uuid4(),
            organization_id=org_b.id,
            name="Sarah Connor OrgB",
            email="sarah.connor.b@cyberdyne.io",
            created_at=now - timedelta(days=1),
            last_seen_at=now,
        )
        session.add(vis_b)
        await session.flush()

        # Conversations for Sarah in Org A:
        # Conv 1: Historical closed ticket
        conv_sarah_old = Conversation(
            id=uuid.uuid4(),
            organization_id=org_a.id,
            visitor_id=vis_1.id,
            status=ConversationStatus.closed,
            title="Old Replacement Request",
            assigned_user_id=staff_a.id,
            created_at=now - timedelta(days=3),
            last_message_at=now - timedelta(days=3),
        )
        # Conv 2: Current active open ticket
        conv_sarah_current = Conversation(
            id=uuid.uuid4(),
            organization_id=org_a.id,
            visitor_id=vis_1.id,
            status=ConversationStatus.open,
            title="Urgent Cyberdyne Inquiry",
            assigned_user_id=staff_a.id,
            created_at=now - timedelta(hours=2),
            last_message_at=now,
        )
        # Conversation for John in Org A
        conv_john = Conversation(
            id=uuid.uuid4(),
            organization_id=org_a.id,
            visitor_id=vis_2.id,
            status=ConversationStatus.open,
            title="General Billing Inquiry",
            created_at=now - timedelta(hours=1),
            last_message_at=now - timedelta(minutes=30),
        )
        # Conversation in Org B
        conv_b = Conversation(
            id=uuid.uuid4(),
            organization_id=org_b.id,
            visitor_id=vis_b.id,
            status=ConversationStatus.open,
            title="Org B Inquiry",
            created_at=now,
            last_message_at=now,
        )
        session.add_all([conv_sarah_old, conv_sarah_current, conv_john, conv_b])
        await session.flush()

        # Messages in Sarah's Old conversation
        m_old_1 = Message(
            conversation_id=conv_sarah_old.id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="I need a refund for my damaged sensor module.",
            created_at=now - timedelta(days=3),
        )
        m_old_2 = Message(
            conversation_id=conv_sarah_old.id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.staff,
            sender_user_id=staff_a.id,
            content="We have processed your refund ticket.",
            created_at=now - timedelta(days=3),
        )

        # Messages in Sarah's Current conversation
        m_curr_1 = Message(
            conversation_id=conv_sarah_current.id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="Can you confirm delivery status for order #8892?",
            created_at=now - timedelta(hours=2),
        )

        # Message in John's conversation
        m_john = Message(
            conversation_id=conv_john.id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="Where can I download my invoice PDF?",
            created_at=now - timedelta(minutes=30),
        )

        session.add_all([m_old_1, m_old_2, m_curr_1, m_john])
        await session.commit()

        token_a = create_access_jwt(str(staff_a.id), "staff", org_id=str(org_a.id))
        token_b = create_access_jwt(str(staff_b.id), "staff", org_id=str(org_b.id))

        return {
            "org_a": org_a,
            "org_b": org_b,
            "staff_a": staff_a,
            "token_a": token_a,
            "staff_b": staff_b,
            "token_b": token_b,
            "vis_sarah": vis_1,
            "vis_john": vis_2,
            "conv_sarah_old": conv_sarah_old,
            "conv_sarah_current": conv_sarah_current,
            "conv_john": conv_john,
            "conv_b": conv_b,
        }


# ==============================================================================
# Item 1: Multi-Attribute Inbox Search Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_search_conversations_by_customer_name(setup_phase_3_env):
    """Searching q='Sarah' returns Sarah's conversations in Org A."""
    token = setup_phase_3_env["token_a"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            "/api/v1/conversations?q=Sarah",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        ids = [item["id"] for item in data["items"]]
        assert str(setup_phase_3_env["conv_sarah_current"].id) in ids
        assert str(setup_phase_3_env["conv_sarah_old"].id) in ids
        assert str(setup_phase_3_env["conv_john"].id) not in ids


@pytest.mark.asyncio
async def test_search_conversations_by_customer_email(setup_phase_3_env):
    """Searching q='cyberdyne.io' matches Sarah's email."""
    token = setup_phase_3_env["token_a"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            "/api/v1/conversations?q=cyberdyne.io",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        ids = [item["id"] for item in data["items"]]
        assert str(setup_phase_3_env["conv_sarah_current"].id) in ids
        assert str(setup_phase_3_env["conv_sarah_old"].id) in ids


@pytest.mark.asyncio
async def test_search_conversations_by_conversation_uuid(setup_phase_3_env):
    """Searching by exact conversation UUID returns that specific conversation."""
    token = setup_phase_3_env["token_a"]
    target_id = str(setup_phase_3_env["conv_john"].id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/conversations?q={target_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == target_id


@pytest.mark.asyncio
async def test_search_conversations_by_customer_id(setup_phase_3_env):
    """Filtering by customer_id returns only conversations of that visitor."""
    token = setup_phase_3_env["token_a"]
    vis_id = str(setup_phase_3_env["vis_john"].id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/conversations?customer_id={vis_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == str(setup_phase_3_env["conv_john"].id)


@pytest.mark.asyncio
async def test_search_conversations_by_message_content(setup_phase_3_env):
    """Searching q='invoice PDF' matches the message inside John's conversation."""
    token = setup_phase_3_env["token_a"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            "/api/v1/conversations?q=invoice%20PDF",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        ids = [item["id"] for item in data["items"]]
        assert str(setup_phase_3_env["conv_john"].id) in ids
        assert str(setup_phase_3_env["conv_sarah_current"].id) not in ids


@pytest.mark.asyncio
async def test_search_multi_tenant_isolation(setup_phase_3_env):
    """Staff in Org B searching 'Sarah' must NEVER see Org A's Sarah conversations."""
    token_b = setup_phase_3_env["token_b"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            "/api/v1/conversations?q=Sarah",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert res.status_code == 200
        data = res.json()
        ids = [item["id"] for item in data["items"]]
        # Must only see Org B conversation
        assert str(setup_phase_3_env["conv_b"].id) in ids
        assert str(setup_phase_3_env["conv_sarah_current"].id) not in ids
        assert str(setup_phase_3_env["conv_sarah_old"].id) not in ids


# ==============================================================================
# Item 2: Customer Past History Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_get_customer_history_for_multi_ticket_visitor(setup_phase_3_env):
    """GET /conversations/{conv_sarah_current}/customer-history returns old conversation."""
    token = setup_phase_3_env["token_a"]
    curr_id = str(setup_phase_3_env["conv_sarah_current"].id)
    old_id = str(setup_phase_3_env["conv_sarah_old"].id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/conversations/{curr_id}/customer-history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["visitor_name"] == "Sarah Connor"
        assert data["visitor_email"] == "sarah.connor@cyberdyne.io"
        assert len(data["past_conversations"]) == 1

        past_item = data["past_conversations"][0]
        assert past_item["id"] == old_id
        assert past_item["status"] == "closed"
        assert past_item["total_messages"] == 2
        assert "refund" in past_item["last_message_preview"]
        assert past_item["assigned_agent_name"] == "Agent Alice"


@pytest.mark.asyncio
async def test_get_customer_history_empty_for_first_time_customer(setup_phase_3_env):
    """GET /conversations/{conv_john}/customer-history returns empty past_conversations."""
    token = setup_phase_3_env["token_a"]
    john_conv_id = str(setup_phase_3_env["conv_john"].id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/conversations/{john_conv_id}/customer-history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["visitor_name"] == "John Doe"
        assert data["past_conversations"] == []


@pytest.mark.asyncio
async def test_get_customer_history_cross_tenant_rejection(setup_phase_3_env):
    """Staff B requesting customer history for Org A conversation gets HTTP 404."""
    token_b = setup_phase_3_env["token_b"]
    org_a_conv_id = str(setup_phase_3_env["conv_sarah_current"].id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/conversations/{org_a_conv_id}/customer-history",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert res.status_code == 404


# ==============================================================================
# Adversarial & Fuzzing Tests (Principal QA Audit)
# ==============================================================================


@pytest.mark.asyncio
async def test_search_fuzz_malicious_and_boundary_inputs(setup_phase_3_env):
    """Fuzz test /conversations?q=... with wildcard injection, SQL probes, and long strings."""
    token = setup_phase_3_env["token_a"]

    adversarial_queries = [
        "%",
        "%%%",
        "_",
        "___",
        "' OR '1'='1",
        "'; DROP TABLE conversations; --",
        "00000000-0000-0000-0000-000000000000",
        "not-a-valid-uuid-at-all",
        "A" * 1000,  # Buffer / long query
        "🔥 Cyberdyne 🤖",
        "\x00nullbyte",
        "\n\r\t",
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for q_payload in adversarial_queries:
            res = await client.get(
                "/api/v1/conversations",
                params={"q": q_payload},
                headers={"Authorization": f"Bearer {token}"},
            )
            # Must return 200 without 500 server crash or SQL syntax exception
            assert res.status_code == 200
            data = res.json()
            assert "items" in data
            assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_customer_history_zero_message_conversations(setup_phase_3_env):
    """Customer history handles conversations with 0 messages safely."""
    token = setup_phase_3_env["token_a"]
    org_a = setup_phase_3_env["org_a"]
    vis_john = setup_phase_3_env["vis_john"]

    # Add empty conversation for John
    async with async_session_factory() as session:
        now = datetime.now(UTC)
        conv_empty = Conversation(
            organization_id=org_a.id,
            visitor_id=vis_john.id,
            status=ConversationStatus.closed,
            title="Empty Historical Ticket",
            created_at=now - timedelta(days=4),
            last_message_at=now - timedelta(days=4),
        )
        session.add(conv_empty)
        await session.commit()
        await session.refresh(conv_empty)
        empty_id = conv_empty.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/conversations/{setup_phase_3_env['conv_john'].id}/customer-history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["past_conversations"]) == 1
        item = data["past_conversations"][0]
        assert item["id"] == str(empty_id)
        assert item["total_messages"] == 0
        assert item["last_message_preview"] is None


@pytest.mark.asyncio
async def test_search_cursor_pagination_with_query(setup_phase_3_env):
    """Cursor pagination behaves predictably when searching with query filter."""
    token = setup_phase_3_env["token_a"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Request with limit=1 to force cursor pagination
        res1 = await client.get(
            "/api/v1/conversations?q=Sarah&limit=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res1.status_code == 200
        data1 = res1.json()
        assert len(data1["items"]) == 1
        assert data1["has_more"] is True
        cursor = data1["next_cursor"]
        assert cursor is not None

        # Fetch page 2 using cursor
        res2 = await client.get(
            f"/api/v1/conversations?q=Sarah&limit=1&cursor={cursor}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res2.status_code == 200
        data2 = res2.json()
        assert len(data2["items"]) == 1
        # Item on page 2 must be different from page 1
        assert data2["items"][0]["id"] != data1["items"][0]["id"]


@pytest.mark.asyncio
async def test_search_wildcard_literal_escaping(setup_phase_3_env):
    """Wildcards like % and _ are escaped so they do not act as catch-all matching patterns."""
    token = setup_phase_3_env["token_a"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # A naked '%' should NOT match everything like an unescaped wildcard
        res = await client.get(
            "/api/v1/conversations?q=%25",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) == 0



