from __future__ import annotations

import hashlib
import hmac
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.channels.base import ChannelEvent, DeliveryResult, DeliveryStatus
from app.channels.security import (
    WebhookDeduplicationGate,
    verify_generic_hmac_sha256,
    verify_whatsapp_signature,
)
from app.config import settings
from app.main import app
from app.models.channel_configuration import ChannelConfiguration
from app.models.organization import Organization
from app.tenancy.context import TenantContext


@pytest.fixture(autouse=True)
def reset_dedupe_gate():
    WebhookDeduplicationGate.reset()
    yield
    WebhookDeduplicationGate.reset()


def test_canonical_event_serialization():
    """Validates ChannelEvent structure and serialization."""
    event = ChannelEvent(
        organization_id=uuid.uuid4(),
        channel_type="email",
        external_user_id="customer@example.com",
        external_message_id="msg-12345",
        direction="inbound",
        text_content="Hello support team",
        metadata={"subject": "Inquiry"},
    )
    assert event.channel_type == "email"
    assert event.direction == "inbound"
    assert event.payload_type == "text"
    assert event.text_content == "Hello support team"

    json_data = event.model_dump()
    assert json_data["external_user_id"] == "customer@example.com"
    assert json_data["metadata"]["subject"] == "Inquiry"


def test_webhook_hmac_signature_validation():
    """Validates cryptographic HMAC-SHA256 signature verifier."""
    secret = "super_secure_webhook_signing_secret_123"
    body = b'{"event":"test_message","data":"hello"}'
    valid_sig = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    # Success case
    headers = {"X-Hub-Signature-256": valid_sig}
    assert verify_whatsapp_signature(headers, body, secret) is True

    # Tampered body fails
    tampered_body = b'{"event":"test_message","data":"hacked"}'
    assert verify_whatsapp_signature(headers, tampered_body, secret) is False

    # Invalid secret fails
    assert verify_whatsapp_signature(headers, body, "wrong_secret") is False

    # Missing header fails
    assert verify_whatsapp_signature({}, body, secret) is False


def test_webhook_replay_attack_rejected():
    """Validates that duplicate event tokens are rejected by the deduplication gate."""
    key = "org1:email:msg-token-abc-999"
    assert WebhookDeduplicationGate.is_duplicate(key) is False
    assert WebhookDeduplicationGate.is_duplicate(key) is True
    assert WebhookDeduplicationGate.is_duplicate(key) is True


@pytest.mark.asyncio
async def test_outbound_dispatch_pipeline_wired_to_staff_reply():
    """Posting staff message to external channel conversation dispatches outbound and updates delivery status."""
    from app.channels.identity import ChannelIdentityResolver
    from app.models.conversation import Conversation, ConversationStatus
    from app.models.message import SenderType
    from app.models.user import User, UserRole
    from app.security.password import hash_password
    from app.services.conversation_service import post_message

    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        org = Organization(slug=f"dispatch-org-{uuid.uuid4().hex[:6]}", name="Dispatch Org", is_active=True)
        session.add(org)
        await session.flush()

        staff = User(
            organization_id=org.id,
            email=f"agent-{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("Pass123!"),
            display_name="Live Agent",
            role=UserRole.staff,
            is_active=True,
        )
        session.add(staff)
        await session.flush()

        # Create external WhatsApp channel conversation
        tokens = TenantContext.set_tenant(org.id, org.slug)
        try:
            ident, conv, _ = await ChannelIdentityResolver.resolve_or_create(
                db=session,
                organization_id=org.id,
                channel_type="whatsapp",
                external_user_id="+14155557788",
            )
            await session.commit()

            # Staff posts message
            msg_resp, is_new = await post_message(
                db=session,
                conversation_id=conv.id,
                content="Hello from our support team! How can I assist?",
                client_message_id=uuid.uuid4(),
                sender_type=SenderType.staff,
                sender_user_id=staff.id,
            )

            assert is_new is True

            # Verify delivery status transitioned to sent
            from app.models.message import Message
            stored_msg = await session.get(Message, msg_resp.id)
            assert stored_msg is not None
            assert stored_msg.delivery_status == "sent"
            assert stored_msg.external_message_id is not None
            assert stored_msg.external_message_id.startswith("wamid.out.")
        finally:
            TenantContext.reset(tokens)

    await engine.dispose()


