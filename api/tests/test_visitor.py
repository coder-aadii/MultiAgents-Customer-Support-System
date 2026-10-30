"""Tests for Sub-phase 1.5 Visitor session boundary."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.errors import NotFoundError
from app.dependencies import validate_visitor_conversation
from app.main import app
from app.models.visitor_session import VisitorSession


@pytest.mark.asyncio
async def test_widget_session_creation_allowed_origin():
    """Verify visitor session is created when allowed Origin is provided."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
            json={"name": "Alice", "email": "alice@example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "session_token" in data
        assert "visitor_id" in data
        assert "conversation_id" in data
        assert "expires_at" in data


@pytest.mark.asyncio
async def test_widget_session_creation_disallowed_origin():
    """Verify 403 FORBIDDEN when request Origin is not allowed."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://malicious-site.com"},
            json={"name": "Attacker"},
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_widget_session_creation_missing_origin():
    """Verify 403 FORBIDDEN when request Origin is missing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            json={"name": "Visitor Without Origin"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_widget_session_resume_with_token():
    """Verify presenting valid session token resumes existing conversation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create initial session
        resp1 = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        data1 = resp1.json()
        token = data1["session_token"]
        conv_id = data1["conversation_id"]
        vis_id = data1["visitor_id"]

        # 2. Reconnect presenting the token
        resp2 = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "Origin": "http://localhost:5174",
                "Authorization": f"Bearer {token}",
            },
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["conversation_id"] == conv_id
        assert data2["visitor_id"] == vis_id
        assert data2["session_token"] == token


@pytest.mark.asyncio
async def test_visitor_conversation_isolation():
    """Verify Visitor A cannot access Visitor B's conversation (returns 404)."""
    fake_conv_id = uuid.uuid4()
    mock_session = VisitorSession(
        id=uuid.uuid4(),
        visitor_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        token_hash="hash",
        origin="http://localhost:5174",
    )

    with pytest.raises(NotFoundError):
        await validate_visitor_conversation(fake_conv_id, mock_session)
