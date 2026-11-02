from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.models.user import User, UserRole
from app.security.password import hash_password
from app.security.tokens import create_access_jwt


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def org_with_roles(async_db: AsyncSession):
    org = Organization(
        slug=f"rbac-org-{uuid.uuid4().hex[:6]}",
        name="RBAC Org",
        is_active=True,
    )
    async_db.add(org)
    await async_db.flush()

    # 1. Owner
    owner_user = User(
        organization_id=org.id,
        email=f"owner-{uuid.uuid4().hex[:4]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Org Owner",
        role=UserRole.admin,
        is_active=True,
    )
    async_db.add(owner_user)
    await async_db.flush()

    owner_mem = OrganizationMembership(
        organization_id=org.id,
        user_id=owner_user.id,
        role=OrgMemberRole.owner.value,
    )
    async_db.add(owner_mem)

    # 2. Admin
    admin_user = User(
        organization_id=org.id,
        email=f"admin-{uuid.uuid4().hex[:4]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Org Admin",
        role=UserRole.admin,
        is_active=True,
    )
    async_db.add(admin_user)
    await async_db.flush()

    admin_mem = OrganizationMembership(
        organization_id=org.id,
        user_id=admin_user.id,
        role=OrgMemberRole.admin.value,
    )
    async_db.add(admin_mem)

    # 3. Agent
    agent_user = User(
        organization_id=org.id,
        email=f"agent-{uuid.uuid4().hex[:4]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Org Agent",
        role=UserRole.staff,
        is_active=True,
    )
    async_db.add(agent_user)
    await async_db.flush()

    agent_mem = OrganizationMembership(
        organization_id=org.id,
        user_id=agent_user.id,
        role=OrgMemberRole.agent.value,
    )
    async_db.add(agent_mem)

    await async_db.commit()
    return org, owner_user, admin_user, agent_user


@pytest.mark.asyncio
async def test_agent_cannot_access_tenant_settings(org_with_roles):
    """An agent attempting to mutate organization settings receives 403 Forbidden."""
    org, owner, admin, agent = org_with_roles

    token = create_access_jwt(
        subject=str(agent.id),
        role=agent.role.value,
        additional_claims={"org_id": str(org.id), "org_slug": org.slug},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/admin/organizations/current",
            headers={"Authorization": f"Bearer {token}", "X-Organization-Slug": org.slug},
            json={"name": "Hacked Org Name"},
        )
        assert resp.status_code == 403
        err = resp.json().get("error", {})
        assert err.get("code") == "FORBIDDEN" or "Requires" in err.get("message", "")


@pytest.mark.asyncio
async def test_admin_can_invite_members(org_with_roles):
    """An admin can invite new members to the organization."""
    org, owner, admin, agent = org_with_roles

    token = create_access_jwt(
        subject=str(admin.id),
        role=admin.role.value,
        additional_claims={"org_id": str(org.id), "org_slug": org.slug},
    )

    new_member_email = f"invited-{uuid.uuid4().hex[:4]}@example.com"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/admin/organizations/current/members",
            headers={"Authorization": f"Bearer {token}", "X-Organization-Slug": org.slug},
            json={
                "email": new_member_email,
                "display_name": "Invited Agent",
                "role": "agent",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["role"] == "agent"
        assert data["organization_id"] == str(org.id)


@pytest.mark.asyncio
async def test_owner_can_configure_sso(org_with_roles):
    """Only owner can configure enterprise SSO parameters."""
    org, owner, admin, agent = org_with_roles

    # Admin trying to configure SSO -> 403
    admin_token = create_access_jwt(
        subject=str(admin.id),
        role=admin.role.value,
        additional_claims={"org_id": str(org.id), "org_slug": org.slug},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp_admin = await client.patch(
            "/api/v1/admin/organizations/current",
            headers={"Authorization": f"Bearer {admin_token}", "X-Organization-Slug": org.slug},
            json={"sso_config": {"provider": "saml", "idp_sso_url": "https://idp.com"}},
        )
        assert resp_admin.status_code == 403

    # Owner configuring SSO -> 200 OK
    owner_token = create_access_jwt(
        subject=str(owner.id),
        role=owner.role.value,
        additional_claims={"org_id": str(org.id), "org_slug": org.slug},
    )

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp_owner = await client.patch(
            "/api/v1/admin/organizations/current",
            headers={"Authorization": f"Bearer {owner_token}", "X-Organization-Slug": org.slug},
            json={"sso_config": {"provider": "saml", "idp_sso_url": "https://idp.com"}},
        )
        assert resp_owner.status_code == 200
        data = resp_owner.json()
        assert data["sso_config_json"]["provider"] == "saml"
