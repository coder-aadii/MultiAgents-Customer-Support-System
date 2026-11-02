from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models.conversation import Conversation, ConversationStatus
from app.models.organization import Organization
from app.models.support_team import SupportTeam
from app.models.user import User, UserRole
from app.models.visitor import Visitor
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
async def hub_setup(async_db: AsyncSession):
    org = Organization(slug=f"hub-org-{uuid.uuid4().hex[:6]}", name="Hub Org", is_active=True)
    async_db.add(org)
    await async_db.flush()

    admin = User(
        organization_id=org.id,
        email=f"super-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Supervisor Jane",
        role=UserRole.admin,
        is_active=True,
    )
    agent = User(
        organization_id=org.id,
        email=f"agent-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Agent Bob",
        role=UserRole.staff,
        is_active=True,
    )
    new_agent = User(
        organization_id=org.id,
        email=f"target-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Target Agent",
        role=UserRole.staff,
        is_active=True,
    )
    team = SupportTeam(organization_id=org.id, name="Escalations Team")
    async_db.add_all([admin, agent, new_agent, team])
    await async_db.flush()

    visitor = Visitor(organization_id=org.id, name="Customer For Hub")
    async_db.add(visitor)
    await async_db.flush()

    conv = Conversation(
        organization_id=org.id,
        visitor_id=visitor.id,
        assigned_user_id=agent.id,
        status=ConversationStatus.staff_active,
    )
    async_db.add(conv)
    await async_db.commit()

    return org, admin, agent, new_agent, team, conv


@pytest.mark.asyncio
async def test_supervisor_can_reassign_conversation(hub_setup):
    """Supervisor can force reassign conversation to a new agent or team."""
    org, admin, agent, new_agent, team, conv = hub_setup

    admin_token = create_access_jwt(
        subject=str(admin.id),
        role=admin.role.value,
        additional_claims={"org_id": str(org.id), "org_slug": org.slug},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/admin/operations/conversations/{conv.id}/assign",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"team_id": str(team.id), "agent_id": str(new_agent.id)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["assigned_team_id"] == str(team.id)
        assert data["assigned_user_id"] == str(new_agent.id)


@pytest.mark.asyncio
async def test_agent_cannot_access_supervisor_console(hub_setup):
    """Standard staff agent without supervisor/admin privileges cannot reassign conversations."""
    org, admin, agent, new_agent, team, conv = hub_setup

    agent_token = create_access_jwt(
        subject=str(agent.id),
        role=agent.role.value,
        additional_claims={"org_id": str(org.id), "org_slug": org.slug},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/admin/operations/conversations/{conv.id}/assign",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"agent_id": str(new_agent.id)},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_agents_roster(hub_setup):
    """Staff or admin can list all agents with presence and workload metrics."""
    org, admin, agent, new_agent, team, conv = hub_setup

    admin_token = create_access_jwt(
        subject=str(admin.id),
        role=admin.role.value,
        additional_claims={"org_id": str(org.id), "org_slug": org.slug},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/admin/operations/agents",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        agents = resp.json()
        assert len(agents) >= 3
        # Check structure
        first = agents[0]
        assert "display_name" in first
        assert "email" in first
        assert "role" in first
        assert "presence" in first
        assert "active_chats" in first
        assert "teams" in first

        # Verify staff member gets 403 Forbidden when trying to list other agents
        agent_token = create_access_jwt(
            subject=str(agent.id),
            role=agent.role.value,
            additional_claims={"org_id": str(org.id), "org_slug": org.slug},
        )
        staff_resp = await client.get(
            "/api/v1/admin/operations/agents",
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert staff_resp.status_code == 403


