from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.channels.adapters.sms import SMSChannelAdapter
from app.channels.identity import ChannelIdentityResolver
from app.config import settings
from app.models.organization import Organization


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def test_org(async_db: AsyncSession) -> Organization:
    org = Organization(
        slug=f"sms-org-{uuid.uuid4().hex[:6]}",
        name="SMS Test Org",
        is_active=True,
    )
    async_db.add(org)
    await async_db.commit()
    return org


@pytest.mark.asyncio
async def test_sms_inbound_message_linking(async_db: AsyncSession, test_org: Organization):
    """Twilio SMS webhook parses sender and links to customer phone identity."""
    adapter = SMSChannelAdapter()
    payload = {
        "From": "+12025550199",
        "Body": "Can I check my tracking number TRK-9900?",
        "MessageSid": "SM1234567890abcdef",
    }

    event = await adapter.parse_inbound_event(payload, test_org.id)
    assert event.channel_type == "sms"
    assert event.external_user_id == "+12025550199"
    assert event.text_content == "Can I check my tracking number TRK-9900?"
    assert event.metadata["opt_out"] is False

    identity, conv, is_new = await ChannelIdentityResolver.resolve_or_create(
        db=async_db,
        organization_id=test_org.id,
        channel_type="sms",
        external_user_id=event.external_user_id,
    )
    await async_db.commit()

    assert identity.external_user_id == "+12025550199"
    assert conv is not None


@pytest.mark.asyncio
async def test_sms_stop_keyword_sets_opt_out(test_org: Organization):
    """Carrier compliance: STOP keyword flags opt_out in metadata."""
    adapter = SMSChannelAdapter()
    for kw in ["STOP", "unsubscribe", "QUIT", "CANCEL"]:
        event = await adapter.parse_inbound_event({"From": "+12025550199", "Body": kw}, test_org.id)
        assert event.metadata["opt_out"] is True

    # START keyword flags opt_in
    event_start = await adapter.parse_inbound_event({"From": "+12025550199", "Body": "START"}, test_org.id)
    assert event_start.metadata["opt_in"] is True

