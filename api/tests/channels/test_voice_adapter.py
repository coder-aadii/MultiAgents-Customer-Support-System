from __future__ import annotations

import uuid

import pytest

from app.channels.adapters.voice import VoiceChannelAdapter


@pytest.mark.asyncio
async def test_voice_transcription_chunking_and_ingestion():
    """Voice adapter parses telephony transcription results and extracts confidence."""
    adapter = VoiceChannelAdapter()
    org_id = uuid.uuid4()
    payload = {
        "CallSid": "CA1234567890abcdef",
        "From": "+18005550199",
        "TranscriptionText": "I would like to check the status of my refund",
        "Confidence": "0.98",
    }

    event = await adapter.parse_inbound_event(payload, org_id)
    assert event.channel_type == "voice"
    assert event.external_user_id == "+18005550199"
    assert event.payload_type == "audio"
    assert event.text_content == "I would like to check the status of my refund"
    assert event.metadata["call_sid"] == "CA1234567890abcdef"
    assert event.metadata["transcription_confidence"] == 0.98

    out = await adapter.send_outbound_message(event, credentials={})
    assert out.success is True
    assert out.metadata["call_sid"] == "CA1234567890abcdef"

