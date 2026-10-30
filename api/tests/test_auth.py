"""Tests for Sub-phase 1.4 Staff authentication."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import async_session_factory
from app.main import app
from app.models.user import User, UserRole
from app.security.cookies import REFRESH_COOKIE_NAME
from app.security.password import hash_password


@pytest.fixture(autouse=True)
async def setup_test_users():
    """Ensure test users exist for all tests in module."""
    async with async_session_factory() as session:
        # Staff user
        res = await session.execute(select(User).where(User.email == "staff@example.com"))
        staff = res.scalar_one_or_none()
        if not staff:
            staff = User(
                id=uuid.uuid4(),
                email="staff@example.com",
                password_hash=hash_password("staffpassword123"),
                display_name="Test Staff",
                role=UserRole.staff,
                is_active=True,
            )
            session.add(staff)
        else:
            staff.password_hash = hash_password("staffpassword123")
            staff.is_active = True

        # Inactive user
        res_in = await session.execute(select(User).where(User.email == "inactive@example.com"))
        inactive = res_in.scalar_one_or_none()
        if not inactive:
            inactive = User(
                id=uuid.uuid4(),
                email="inactive@example.com",
                password_hash=hash_password("inactivepassword123"),
                display_name="Inactive User",
                role=UserRole.staff,
                is_active=False,
            )
            session.add(inactive)
        else:
            inactive.is_active = False

        await session.commit()


@pytest.mark.asyncio
async def test_login_success():
    """Test successful login with email/password, returning JWT and setting refresh cookie."""
    """Test successful login with email/password, returning JWT and setting refresh cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "STAFF@example.com", "password": "staffpassword123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == "staff@example.com"
        assert data["user"]["role"] == "staff"

        # Verify HttpOnly cookie
        cookie_header = resp.headers.get("set-cookie", "")
        assert REFRESH_COOKIE_NAME in cookie_header
        assert "HttpOnly" in cookie_header
        assert "Path=/api/v1/auth" in cookie_header


@pytest.mark.asyncio
async def test_login_failure_invalid_password():
    """Test login failure with incorrect password returns 401 UNAUTHORIZED."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "staff@example.com", "password": "wrongpassword"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "UNAUTHORIZED"
        assert "Invalid email or password" in data["error"]["message"]


@pytest.mark.asyncio
async def test_login_failure_unknown_email():
    """Test login failure with unknown email returns identical 401 UNAUTHORIZED."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "anypassword"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "UNAUTHORIZED"
        assert "Invalid email or password" in data["error"]["message"]


@pytest.mark.asyncio
async def test_login_failure_inactive_user():
    """Test login failure with inactive user returns identical 401 UNAUTHORIZED."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "inactive@example.com", "password": "inactivepassword123"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_get_me_endpoint():
    """Test GET /api/v1/auth/me returns current user details."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First login to get access token
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "staff@example.com", "password": "staffpassword123"},
        )
        token = login_resp.json()["access_token"]

        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == "staff@example.com"


@pytest.mark.asyncio
async def test_refresh_token_rotation():
    """Test refresh token rotation issues new access token and new refresh cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Login to obtain refresh token cookie
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "staff@example.com", "password": "staffpassword123"},
        )
        old_token = login_resp.json()["access_token"]

        # 2. Call refresh endpoint
        refresh_resp = await client.post("/api/v1/auth/refresh")
        assert refresh_resp.status_code == 200
        new_data = refresh_resp.json()
        assert "access_token" in new_data
        assert new_data["access_token"] != old_token

        # Verify new cookie set
        assert REFRESH_COOKIE_NAME in refresh_resp.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_refresh_token_reuse_revocation():
    """Test reusing a rotated refresh token revokes entire family."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Login
        await client.post(
            "/api/v1/auth/login",
            json={"email": "staff@example.com", "password": "staffpassword123"},
        )
        # Extract initial cookie
        initial_cookie = client.cookies.get(REFRESH_COOKIE_NAME)

        # 2. First refresh (legitimate rotation)
        refresh1 = await client.post("/api/v1/auth/refresh")
        assert refresh1.status_code == 200

        # 3. Simulate attacker presenting initial_cookie again (replay attack)
        client.cookies.set(REFRESH_COOKIE_NAME, initial_cookie)
        replay_resp = await client.post("/api/v1/auth/refresh")
        assert replay_resp.status_code == 401
        assert "Revoked token reuse detected" in replay_resp.json()["error"]["message"]

        # 4. Legitimate user trying to refresh now fails because entire family was revoked
        refresh2 = await client.post("/api/v1/auth/refresh")
        assert refresh2.status_code == 401


@pytest.mark.asyncio
async def test_logout():
    """Test logout revokes token and clears refresh cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/v1/auth/login",
            json={"email": "staff@example.com", "password": "staffpassword123"},
        )

        logout_resp = await client.post("/api/v1/auth/logout")
        assert logout_resp.status_code == 200
        assert logout_resp.json() == {"status": "ok"}

        # Trying to refresh after logout must fail
        refresh_resp = await client.post("/api/v1/auth/refresh")
        assert refresh_resp.status_code == 401
