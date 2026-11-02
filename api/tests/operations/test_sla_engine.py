from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.conversation import Conversation, ConversationStatus
from app.models.organization import Organization
from app.models.sla_policy import SLAPolicy
from app.models.visitor import Visitor
from app.operations.sla import SLAEngine


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def sla_setup(async_db: AsyncSession):
    org = Organization(slug=f"sla-org-{uuid.uuid4().hex[:6]}", name="SLA Org", is_active=True)
    async_db.add(org)
    await async_db.flush()

    policy = SLAPolicy(
        organization_id=org.id,
        name="Standard Tier 1 SLA",
        priority="high",
        first_response_target_minutes=60,
        resolution_target_minutes=240,
        business_hours_schedule_json={"start_hour": 9, "end_hour": 17},
        is_active=True,
    )
    async_db.add(policy)
    await async_db.flush()

    visitor = Visitor(organization_id=org.id, name="SLA Customer")
    async_db.add(visitor)
    await async_db.flush()

    now = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)  # Monday 10:00 AM
    conv = Conversation(
        organization_id=org.id,
        visitor_id=visitor.id,
        status=ConversationStatus.open,
        sla_policy_id=policy.id,
        created_at=now,
        last_message_at=now,
    )
    async_db.add(conv)
    await async_db.commit()

    return org, policy, conv, now


def test_sla_calculates_business_hours_accurately():
    """Business hours calculation strictly skips weekend and overnight non-working hours."""
    # Monday 10:00 to Monday 12:00 = 120 business minutes
    t1 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    mins = SLAEngine.calculate_business_minutes(t1, t2, {"start_hour": 9, "end_hour": 17})
    assert mins == 120.0

    # Friday 16:00 to Monday 10:00 = 60 mins Friday (16-17) + 60 mins Monday (9-10) = 120 mins
    # Nov 6, 2026 is Friday, Nov 9, 2026 is Monday
    fri = datetime(2026, 11, 6, 16, 0, tzinfo=UTC)
    mon = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    weekend_mins = SLAEngine.calculate_business_minutes(fri, mon, {"start_hour": 9, "end_hour": 17})
    assert weekend_mins == 120.0


@pytest.mark.asyncio
async def test_sla_triggers_warning_threshold_alert(async_db: AsyncSession, sla_setup):
    """At 80% of SLA target time (48 minutes of 60m), status transitions to warning."""
    org, policy, conv, start_time = sla_setup

    # Check at 50 minutes elapsed
    eval_time = start_time + timedelta(minutes=50)
    status, elapsed, target = await SLAEngine.evaluate_sla(async_db, conv.id, now=eval_time)

    assert status == "warning"
    assert elapsed == 50.0
    assert target == 60.0
    assert conv.sla_breached is False


@pytest.mark.asyncio
async def test_sla_marks_conversation_breached(async_db: AsyncSession, sla_setup):
    """When elapsed business time reaches target (65m > 60m), conversation is marked breached."""
    org, policy, conv, start_time = sla_setup

    eval_time = start_time + timedelta(minutes=65)
    status, elapsed, target = await SLAEngine.evaluate_sla(async_db, conv.id, now=eval_time)

    assert status == "breached"
    assert elapsed == 65.0
    await async_db.refresh(conv)
    assert conv.sla_breached is True


@pytest.mark.asyncio
async def test_sla_background_scan_detects_breaches(async_db: AsyncSession, sla_setup):
    """Periodic SLA scan evaluates all active conversations and updates breached flags."""
    org, policy, conv, start_time = sla_setup

    # Run scan at 70 minutes after creation
    eval_time = start_time + timedelta(minutes=70)
    results = await SLAEngine.run_sla_scan(async_db, org_id=org.id, now=eval_time)

    assert len(results) >= 1
    target_result = next(r for r in results if r["conversation_id"] == str(conv.id))
    assert target_result["status"] == "breached"
    assert target_result["sla_breached"] is True

    await async_db.refresh(conv)
    assert conv.sla_breached is True


