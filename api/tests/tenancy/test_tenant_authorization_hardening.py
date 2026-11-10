from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.models.user import User, UserRole
from app.models.visitor_session import VisitorSession
from app.security.customer_auth import create_customer_jwt
from app.security.password import hash_password
from app.security.tokens import create_access_jwt
from app.tenancy.context import DEFAULT_ORGANIZATION_ID


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def two_orgs_with_users(async_db: AsyncSession):
    org_a = Organization(
        slug=f"org-a-{uuid.uuid4().hex[:6]}",
        name="Organization Alpha",
        is_active=True,
        settings_json={"theme": "blue", "confidential": "alpha-secret"},
        sso_config_json={"idp_metadata_url": "https://idp.alpha.example.com", "client_secret": "alpha-secret-key"},
    )
    org_b = Organization(
        slug=f"org-b-{uuid.uuid4().hex[:6]}",
        name="Organization Beta",
        is_active=True,
        settings_json={"theme": "green", "confidential": "beta-secret"},
        sso_config_json={"idp_metadata_url": "https://idp.beta.example.com", "client_secret": "beta-secret-key"},
    )
    async_db.add_all([org_a, org_b])
    await async_db.flush()

    user_a = User(
        organization_id=org_a.id,
        email=f"agent-a-{uuid.uuid4().hex[:4]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Agent Alpha",
        role=UserRole.staff,
        is_active=True,
    )
    user_b = User(
        organization_id=org_b.id,
        email=f"agent-b-{uuid.uuid4().hex[:4]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Agent Beta",
        role=UserRole.staff,
        is_active=True,
    )
    async_db.add_all([user_a, user_b])
    await async_db.flush()

    mem_a = OrganizationMembership(
        organization_id=org_a.id,
        user_id=user_a.id,
        role=OrgMemberRole.agent.value,
    )
    mem_b = OrganizationMembership(
        organization_id=org_b.id,
        user_id=user_b.id,
        role=OrgMemberRole.agent.value,
    )
    async_db.add_all([mem_a, mem_b])
    await async_db.commit()

    return org_a, org_b, user_a, user_b


@pytest.mark.asyncio
async def test_unauthenticated_organization_current_rejected(two_orgs_with_users):
    """Anonymous callers cannot harvest organization settings or SSO credentials via header injection."""
    org_a, _, _, _ = two_orgs_with_users

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/admin/organizations/current",
            headers={"X-Organization-Slug": org_a.slug},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data.get("error", {}).get("code") == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_authenticated_agent_can_read_own_organization(two_orgs_with_users):
    """Authenticated staff member can read their own organization profile."""
    org_a, _, user_a, _ = two_orgs_with_users

    token_a = create_access_jwt(
        subject=str(user_a.id),
        role=user_a.role.value,
        org_id=str(org_a.id),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Request with matching or omitted header
        resp = await client.get(
            "/api/v1/admin/organizations/current",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["slug"] == org_a.slug
        assert data["id"] == str(org_a.id)
        assert data["settings_json"]["theme"] == "blue"


@pytest.mark.asyncio
async def test_spoofed_organization_id_header_rejected(two_orgs_with_users):
    """Presenting an Org A token with an Org B X-Organization-Id header is rejected with 403."""
    org_a, org_b, user_a, _ = two_orgs_with_users

    token_a = create_access_jwt(
        subject=str(user_a.id),
        role=user_a.role.value,
        org_id=str(org_a.id),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/admin/organizations/current",
            headers={
                "Authorization": f"Bearer {token_a}",
                "X-Organization-Id": str(org_b.id),
            },
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data.get("error", {}).get("code") == "FORBIDDEN"
        assert "mismatch" in data.get("error", {}).get("message", "").lower()


@pytest.mark.asyncio
async def test_spoofed_organization_slug_header_rejected(two_orgs_with_users):
    """Presenting an Org A token with an Org B X-Organization-Slug header is rejected with 403."""
    org_a, org_b, user_a, _ = two_orgs_with_users

    token_a = create_access_jwt(
        subject=str(user_a.id),
        role=user_a.role.value,
        org_id=str(org_a.id),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/admin/organizations/current",
            headers={
                "Authorization": f"Bearer {token_a}",
                "X-Organization-Slug": org_b.slug,
            },
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data.get("error", {}).get("code") == "FORBIDDEN"
        assert "mismatch" in data.get("error", {}).get("message", "").lower()


@pytest.mark.asyncio
async def test_anonymous_widget_session_preserves_legitimate_tenant_slug(two_orgs_with_users):
    """Anonymous visitors can legitimately initialize widget sessions under a tenant slug."""
    org_a, _, _, _ = two_orgs_with_users

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"X-Organization-Slug": org_a.slug, "Origin": "http://localhost:5174"},
            json={"name": "Alice Anonymous"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_assurance_level"] == 0
        assert data["session_token"] is not None


@pytest.mark.asyncio
async def test_anonymous_widget_session_preserves_default_olist_fallback():
    """Anonymous visitors without organization headers fall back safely to default Olist tenant."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
            json={"name": "Olist Shopper"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_assurance_level"] == 0
        assert data["session_token"] is not None


@pytest.mark.asyncio
async def test_customer_token_cross_tenant_isolation(two_orgs_with_users):
    """A customer token bound to Org A is rejected when presented to Org B widget session."""
    org_a, org_b, _, _ = two_orgs_with_users

    # Customer JWT generated with custom claims including Org A id
    token_for_org_a = create_customer_jwt(
        customer_id="cust-12345",
        email="customer@example.com",
        custom_claims={"org_id": str(org_a.id)},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Present token for Org A while establishing session in Org B
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "X-Organization-Slug": org_b.slug,
                "Origin": "http://localhost:5174",
                "x-customer-token": token_for_org_a,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # The cross-tenant token must be dropped: IAL must remain 0 (anonymous) and verified_customer_id None
        assert data["identity_assurance_level"] == 0
        assert data["verified_customer_id"] is None


@pytest.mark.asyncio
async def test_customer_token_same_tenant_accepted(two_orgs_with_users):
    """A customer token matching the active tenant is accepted with IAL 2."""
    org_a, _, _, _ = two_orgs_with_users

    token_for_org_a = create_customer_jwt(
        customer_id="cust-legit-777",
        email="customer-alpha@example.com",
        custom_claims={"org_id": str(org_a.id)},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "X-Organization-Slug": org_a.slug,
                "Origin": "http://localhost:5174",
                "x-customer-token": token_for_org_a,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_assurance_level"] == 2
        assert data["verified_customer_id"] == "cust-legit-777"


@pytest.mark.asyncio
async def test_visitor_session_cross_tenant_resumption_prevented(two_orgs_with_users):
    """A visitor session token created under Org A cannot be resumed under Org B."""
    org_a, org_b, _, _ = two_orgs_with_users

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create session in Org A
        resp_a = await client.post(
            "/api/v1/widget/sessions",
            headers={"X-Organization-Slug": org_a.slug, "Origin": "http://localhost:5174"},
            json={"name": "Session Switcher"},
        )
        assert resp_a.status_code == 200
        session_a_token = resp_a.json()["session_token"]
        conv_a_id = resp_a.json()["conversation_id"]

        # 2. Try to resume session_a_token in Org B
        resp_b = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "Authorization": f"Bearer {session_a_token}",
                "X-Organization-Slug": org_b.slug,
                "Origin": "http://localhost:5174",
            },
        )
        assert resp_b.status_code == 200
        data_b = resp_b.json()
        # Must NOT resume Org A's conversation
        assert data_b["conversation_id"] != conv_a_id

