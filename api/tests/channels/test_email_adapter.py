from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.channels.adapters.email import EmailChannelAdapter, strip_email_quoted_reply
from app.channels.base import ChannelEvent
from app.channels.identity import ChannelIdentityResolver
from app.config import settings
from app.models.channel_identity import ChannelIdentity
from app.models.conversation import Conversation
from app.models.organization import Organization
from app.models.visitor import Visitor


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
        slug=f"email-org-{uuid.uuid4().hex[:6]}",
        name="Email Test Org",
        is_active=True,
    )
    async_db.add(org)
    await async_db.commit()
    return org


def test_email_strips_quoted_replies_and_signatures():
    """Email body cleaner removes On ... wrote: headers and standard quote markers."""
    raw_email = (
        "Thank you for the update. My order arrived today!\n\n"
        "On Mon, Nov 2, 2026 at 10:00 AM Support <support@store.com> wrote:\n"
        "> Hello, your order has shipped and will arrive soon.\n"
        "> Let us know if you need any further assistance.\n"
    )
    cleaned = strip_email_quoted_reply(raw_email)
    assert cleaned == "Thank you for the update. My order arrived today!"


@pytest.mark.asyncio
async def test_email_inbound_parses_sender_and_body(test_org: Organization):
    """Email adapter accurately parses SendGrid inbound webhook payload."""
    adapter = EmailChannelAdapter()
    payload = {
        "from": "Alice Customer <alice@example.com>",
        "subject": "Question regarding return policy",
        "text": "Hello, how many days do I have to return an unopened item?",
        "Message-ID": "<msg-unique-id-12345@mail.example.com>",
    }

    event = await adapter.parse_inbound_event(payload, test_org.id)
    assert event.channel_type == "email"
    assert event.external_user_id == "alice@example.com"
    assert event.external_message_id == "<msg-unique-id-12345@mail.example.com>"
    assert "how many days" in event.text_content
    assert event.metadata["subject"] == "Question regarding return policy"


@pytest.mark.asyncio
async def test_email_threading_matches_existing_conversation(
    async_db: AsyncSession, test_org: Organization
):
    """Inbound email from existing sender routes to their existing active conversation."""
    adapter = EmailChannelAdapter()
    user_email = f"bob-{uuid.uuid4().hex[:6]}@example.com"

    # Message 1 creates conversation
    id1, conv1, is_new1 = await ChannelIdentityResolver.resolve_or_create(
        db=async_db,
        organization_id=test_org.id,
        channel_type=adapter.channel_type,
        external_user_id=user_email,
        profile_metadata={"name": "Bob Smith"},
    )
    await async_db.commit()
    assert is_new1 is True

    # Message 2 resolves to same conversation
    id2, conv2, is_new2 = await ChannelIdentityResolver.resolve_or_create(
        db=async_db,
        organization_id=test_org.id,
        channel_type=adapter.channel_type,
        external_user_id=user_email,
    )
    assert is_new2 is False
    assert id1.id == id2.id
    assert conv1.id == conv2.id


@pytest.mark.asyncio
async def test_email_outbound_dispatch_sets_reply_headers():
    """Outbound email dispatch sets References and In-Reply-To headers."""
    adapter = EmailChannelAdapter()
    event = ChannelEvent(
        organization_id=uuid.uuid4(),
        channel_type="email",
        external_user_id="customer@example.com",
        external_conversation_id="<parent-msg-id@mail.com>",
        external_message_id="<curr-msg-id>",
        direction="outbound",
        text_content="Here is your requested return shipping label.",
    )

    res = await adapter.send_outbound_message(event, credentials={})
    assert res.success is True
    assert res.metadata["to"] == "customer@example.com"
    assert res.metadata["in_reply_to"] == "<parent-msg-id@mail.com>"
    assert res.metadata["references"] == "<parent-msg-id@mail.com>"

