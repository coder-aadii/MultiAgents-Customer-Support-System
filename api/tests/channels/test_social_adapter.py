from __future__ import annotations

import uuid

import pytest

from app.channels.adapters.social import SocialChannelAdapter


@pytest.mark.asyncio
async def test_slack_interactive_payload_parsing():
    """Social adapter parses Slack interactive block action and thread timestamps."""
    adapter = SocialChannelAdapter()
    org_id = uuid.uuid4()
    payload = {
        "user_id": "U123456",
        "text": "Fallback text",
        "ts": "1678901234.000100",
        "actions": [{"value": "CONFIRM CANCEL ORD-1001"}],
    }

    event = await adapter.parse_inbound_event(payload, org_id)
    assert event.channel_type == "social"
    assert event.external_user_id == "U123456"
    assert event.text_content == "CONFIRM CANCEL ORD-1001"
    assert event.external_message_id == "1678901234.000100"

