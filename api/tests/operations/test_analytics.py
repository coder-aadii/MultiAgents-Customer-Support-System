from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.conversation import Conversation, ConversationStatus
from app.models.csat_survey import CSATSurvey
from app.models.organization import Organization
from app.models.support_team import SupportTeam
from app.models.visitor import Visitor
from app.operations.analytics import AnalyticsService


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def analytics_setup(async_db: AsyncSession):
    org = Organization(slug=f"analytics-org-{uuid.uuid4().hex[:6]}", name="Analytics Org", is_active=True)
    async_db.add(org)
    await async_db.flush()

    team = SupportTeam(organization_id=org.id, name="Billing Team")
    async_db.add(team)
    await async_db.flush()

    vis = Visitor(organization_id=org.id, name="Analytics Visitor")
    async_db.add(vis)
    await async_db.flush()

    now = datetime.now(UTC)
    c1 = Conversation(
        organization_id=org.id,
        visitor_id=vis.id,
        assigned_team_id=team.id,
        status=ConversationStatus.staff_active,
        created_at=now - timedelta(minutes=30),
        first_response_at=now - timedelta(minutes=20),  # 10m FRT
        resolved_at=now,  # 30m RT
        sla_breached=False,
    )
    c2 = Conversation(
        organization_id=org.id,
        visitor_id=vis.id,
        assigned_team_id=team.id,
        status=ConversationStatus.closed,
        created_at=now - timedelta(minutes=60),
        first_response_at=now - timedelta(minutes=40),  # 20m FRT
        resolved_at=now,  # 60m RT
        sla_breached=True,
    )
    async_db.add_all([c1, c2])
    await async_db.flush()

    # CSAT ratings: 5 and 3 -> Average = 4.0
    s1 = CSATSurvey(organization_id=org.id, conversation_id=c1.id, rating=5, comment="Great")
    s2 = CSATSurvey(organization_id=org.id, conversation_id=c2.id, rating=3, comment="Okay")
    async_db.add_all([s1, s2])
    await async_db.commit()

    return org, team, c1, c2


@pytest.mark.asyncio
async def test_analytics_computes_accurate_frt_and_csat_averages(
    async_db: AsyncSession, analytics_setup
):
    """AnalyticsService aggregates real-time queue states and historical KPIs correctly."""
    org, team, c1, c2 = analytics_setup

    realtime = await AnalyticsService.get_realtime_analytics(async_db, org.id)
    assert realtime["active_chats"] == 1
    assert realtime["closed_chats"] == 1
    assert realtime["sla_breaches"] == 1
    assert len(realtime["queues"]) == 1
    assert realtime["queues"][0]["team_name"] == "Billing Team"
    assert realtime["queues"][0]["depth"] == 2

    metrics = await AnalyticsService.get_historical_metrics(async_db, org.id)
    assert metrics["total_csat_surveys"] == 2
    assert metrics["average_csat_score"] == 4.0
    assert metrics["csat_distribution"][5] == 1
    assert metrics["csat_distribution"][3] == 1
    # Average FRT: (10m + 20m) / 2 = 15m
    assert metrics["average_first_response_minutes"] == 15.0
    # Average RT: (30m + 60m) / 2 = 45m
    assert metrics["average_resolution_minutes"] == 45.0

