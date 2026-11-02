from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.channels.adapters.whatsapp import WhatsAppChannelAdapter
from app.channels.base import DeliveryStatus
from app.channels.identity import ChannelIdentityResolver
from app.config import settings
from app.main import app
from app.models.channel_configuration import ChannelConfiguration
from app.models.message import Message
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
        slug=f"wa-org-{uuid.uuid4().hex[:6]}",
        name="WhatsApp Test Org",
        is_active=True,
    )
    async_db.add(org)
    await async_db.commit()
    return org


def test_whatsapp_webhook_verification_handshake():
    """Meta Cloud API webhook handshake matches verify token and returns challenge text."""
    adapter = WhatsAppChannelAdapter()
    token = "my_meta_secret_verify_token"

    # Valid challenge
    challenge = adapter.verify_webhook_challenge(
        mode="subscribe",
        verify_token=token,
        challenge="1158201444",
        configured_token=token,
    )
    assert challenge == "1158201444"

    # Invalid token rejected
    invalid = adapter.verify_webhook_challenge(
        mode="subscribe",
        verify_token="wrong_token",
        challenge="1158201444",
        configured_token=token,
    )
    assert invalid is None


@pytest.mark.asyncio
async def test_whatsapp_inbound_message_creates_session(
    async_db: AsyncSession, test_org: Organization
):
    """WhatsApp incoming message correctly maps phone number to visitor and conversation."""
    adapter = WhatsAppChannelAdapter()
    phone = "+14155552671"
    wa_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "10001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "15550254483"},
                            "contacts": [{"profile": {"name": "Maria Garcia"}, "wa_id": phone}],
                            "messages": [
                                {
                                    "from": phone,
                                    "id": "wamid.HBgLMTQxNTU1NTI2NzEVAgASGBQz",
                                    "timestamp": "1678901234",
                                    "text": {"body": "Where is my package?"},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    event = await adapter.parse_inbound_event(wa_payload, test_org.id)
    assert event.channel_type == "whatsapp"
    assert event.external_user_id == phone
    assert event.text_content == "Where is my package?"
    assert event.metadata["profile_name"] == "Maria Garcia"

    # Resolve identity
    identity, conv, is_new = await ChannelIdentityResolver.resolve_or_create(
        db=async_db,
        organization_id=test_org.id,
        channel_type=event.channel_type,
        external_user_id=event.external_user_id,
        profile_metadata=event.metadata,
    )
    await async_db.commit()

    assert identity.channel_type == "whatsapp"
    assert identity.external_user_id == phone
    assert conv is not None


@pytest.mark.asyncio
async def test_whatsapp_delivery_receipt_updates_message_status(
    async_db: AsyncSession, test_org: Organization
):
    """Outbound WhatsApp message tracks delivery receipt state."""
    adapter = WhatsAppChannelAdapter()
    event = await adapter.parse_inbound_event(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "+14155559999"}],
                                "messages": [{"from": "+14155559999", "id": "wam-in-1", "text": {"body": "hi"}}],
                            }
                        }
                    ]
                }
            ]
        },
        test_org.id,
    )
    res = await adapter.send_outbound_message(event, credentials={})
    assert res.success is True
    assert res.delivery_status == DeliveryStatus.SENT
    assert res.external_message_id.startswith("wamid.out.")

