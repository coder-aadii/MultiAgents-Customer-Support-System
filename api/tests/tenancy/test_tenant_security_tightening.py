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
async def tightening_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def tenant_isolation_env(tightening_db: AsyncSession):
    org_target = Organization(
        slug=f"org-target-{uuid.uuid4().hex[:6]}",
        name="Target Organization",
        is_active=True,
        settings_json={"theme": "purple"},
    )
    org_attacker = Organization(
        slug=f"org-attacker-{uuid.uuid4().hex[:6]}",
        name="Attacker Organization",
        is_active=True,
        settings_json={"theme": "red"},
    )
    tightening_db.add_all([org_target, org_attacker])
    await tightening_db.flush()

    user_attacker = User(
        organization_id=org_attacker.id,
        email=f"attacker-{uuid.uuid4().hex[:4]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Attacking User",
        role=UserRole.staff,
        is_active=True,
    )
    tightening_db.add(user_attacker)
    await tightening_db.flush()

    mem_attacker = OrganizationMembership(
        organization_id=org_attacker.id,
        user_id=user_attacker.id,
        role=OrgMemberRole.agent.value,
    )
    tightening_db.add(mem_attacker)
    await tightening_db.commit()

    return org_target, org_attacker, user_attacker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "private_endpoint",
    [
        "/api/v1/admin/organizations/current",
        "/api/v1/conversations",
        "/api/v1/handoffs",
        "/api/v1/knowledge/documents",
        "/api/v1/admin/actions/pending",
    ],
)
async def test_unauthenticated_private_routes_reject_spoofed_headers(
    tenant_isolation_env, private_endpoint: str
):
    """Assert unauthenticated callers with spoofed X-Organization-Id / Slug receive 401 Unauthorized."""
    org_target, _, _ = tenant_isolation_env

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Spoofed X-Organization-Id
        resp_id = await client.get(
            private_endpoint,
            headers={"X-Organization-Id": str(org_target.id)},
        )
        assert resp_id.status_code == 401, f"{private_endpoint} did not return 401 with spoofed X-Organization-Id"

        # Spoofed X-Organization-Slug
        resp_slug = await client.get(
            private_endpoint,
            headers={"X-Organization-Slug": org_target.slug},
        )
        assert resp_slug.status_code == 401, f"{private_endpoint} did not return 401 with spoofed X-Organization-Slug"


@pytest.mark.asyncio
async def test_authenticated_user_spoofed_org_headers_forbidden(tenant_isolation_env):
    """Authenticated user cannot spoof headers pointing to another organization."""
    org_target, org_attacker, user_attacker = tenant_isolation_env

    token = create_access_jwt(
        subject=str(user_attacker.id),
        role=user_attacker.role.value,
        org_id=str(org_attacker.id),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Spoofed X-Organization-Id
        resp_id = await client.get(
            "/api/v1/admin/organizations/current",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Organization-Id": str(org_target.id),
            },
        )
        assert resp_id.status_code == 403

        # 2. Spoofed X-Organization-Slug
        resp_slug = await client.get(
            "/api/v1/admin/organizations/current",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Organization-Slug": org_target.slug,
            },
        )
        assert resp_slug.status_code == 403
