from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.conversation import Conversation, ConversationStatus
from app.models.organization import Organization
from app.models.support_team import SupportTeam
from app.models.team_membership import TeamMembership
from app.models.user import User, UserRole
from app.models.visitor import Visitor
from app.operations.routing import CapacityRoutingEngine
from app.operations.teams import AgentPresence, TeamService
from app.security.password import hash_password


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def clean_presence():
    TeamService.reset_presence()
    yield
    TeamService.reset_presence()


@pytest_asyncio.fixture
async def routing_setup(async_db: AsyncSession):
    org = Organization(slug=f"rout-org-{uuid.uuid4().hex[:6]}", name="Routing Org", is_active=True)
    async_db.add(org)
    await async_db.flush()

    team = SupportTeam(organization_id=org.id, name="Tier 1 Support", routing_strategy="least_busy")
    async_db.add(team)
    await async_db.flush()

    agent1 = User(
        organization_id=org.id,
        email=f"agent1-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Agent One",
        role=UserRole.staff,
        is_active=True,
    )
    agent2 = User(
        organization_id=org.id,
        email=f"agent2-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Agent Two",
        role=UserRole.staff,
        is_active=True,
    )
    async_db.add_all([agent1, agent2])
    await async_db.flush()

    mem1 = TeamMembership(team_id=team.id, user_id=agent1.id, max_concurrent_chats=2, skills=["billing", "en"])
    mem2 = TeamMembership(team_id=team.id, user_id=agent2.id, max_concurrent_chats=5, skills=["technical", "es"])
    async_db.add_all([mem1, mem2])
    await async_db.flush()

    visitor = Visitor(organization_id=org.id, name="Test Customer")
    async_db.add(visitor)
    await async_db.flush()

    conv = Conversation(organization_id=org.id, visitor_id=visitor.id, status=ConversationStatus.open)
    async_db.add(conv)
    await async_db.commit()

    return org, team, agent1, agent2, conv


@pytest.mark.asyncio
async def test_least_busy_routing_assigns_to_available_agent(
    async_db: AsyncSession, routing_setup
):
    """Least-busy routing selects the agent with lowest concurrent chat load."""
    org, team, agent1, agent2, conv = routing_setup

    TeamService.set_agent_presence(agent1.id, AgentPresence.ONLINE)
    TeamService.set_agent_presence(agent2.id, AgentPresence.ONLINE)

    assigned_id = await CapacityRoutingEngine.route_conversation(async_db, conv.id, team.id)
    assert assigned_id in (agent1.id, agent2.id)

    # Conversation state updated
    await async_db.refresh(conv)
    assert conv.assigned_team_id == team.id
    assert conv.assigned_user_id == assigned_id
    assert conv.status == ConversationStatus.staff_active


@pytest.mark.asyncio
async def test_agent_at_max_capacity_is_skipped(
    async_db: AsyncSession, routing_setup
):
    """When Agent 1 is at maximum capacity, routing automatically selects Agent 2."""
    org, team, agent1, agent2, conv = routing_setup

    TeamService.set_agent_presence(agent1.id, AgentPresence.ONLINE)
    TeamService.set_agent_presence(agent2.id, AgentPresence.ONLINE)

    # Assign 2 active chats to Agent 1 (filling their max_concurrent_chats = 2)
    vis = Visitor(organization_id=org.id, name="Filler Vis")
    async_db.add(vis)
    await async_db.flush()

    c1 = Conversation(organization_id=org.id, visitor_id=vis.id, assigned_user_id=agent1.id, status=ConversationStatus.staff_active)
    c2 = Conversation(organization_id=org.id, visitor_id=vis.id, assigned_user_id=agent1.id, status=ConversationStatus.staff_active)
    async_db.add_all([c1, c2])
    await async_db.commit()

    assigned_id = await CapacityRoutingEngine.route_conversation(async_db, conv.id, team.id)
    assert assigned_id == agent2.id


@pytest.mark.asyncio
async def test_skill_based_routing_matches_agent_tags(
    async_db: AsyncSession, routing_setup
):
    """Routing with required_skills assigns strictly to the member having those skills."""
    org, team, agent1, agent2, conv = routing_setup

    TeamService.set_agent_presence(agent1.id, AgentPresence.ONLINE)
    TeamService.set_agent_presence(agent2.id, AgentPresence.ONLINE)

    # Technical skill belongs strictly to Agent 2
    assigned_tech = await CapacityRoutingEngine.route_conversation(
        async_db, conv.id, team.id, required_skills=["technical"]
    )
    assert assigned_tech == agent2.id

    # Billing skill belongs strictly to Agent 1
    assigned_billing = await CapacityRoutingEngine.route_conversation(
        async_db, conv.id, team.id, required_skills=["billing"]
    )
    assert assigned_billing == agent1.id


@pytest.mark.asyncio
async def test_handoff_escalation_triggers_auto_routing(
    async_db: AsyncSession, routing_setup
):
    """Creating a handoff automatically routes conversation to available agent."""
    from app.models.handoff import HandoffReason, HandoffStatus
    from app.services.assistant_service import create_or_reuse_handoff

    org, team, agent1, agent2, conv = routing_setup
    TeamService.set_agent_presence(agent1.id, AgentPresence.ONLINE)

    handoff = await create_or_reuse_handoff(
        db=async_db,
        conversation_id=conv.id,
        reason=HandoffReason.visitor_requested,
        summary="Customer wants human assistance",
    )
    await async_db.commit()

    assert handoff.status == HandoffStatus.claimed
    assert handoff.claimed_by_user_id == agent1.id

    await async_db.refresh(conv)
    assert conv.status == ConversationStatus.staff_active
    assert conv.assigned_user_id == agent1.id


