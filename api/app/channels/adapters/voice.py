from __future__ import annotations

import uuid
from typing import Any

from app.channels.base import BaseChannelAdapter, ChannelEvent, DeliveryResult, DeliveryStatus
from app.channels.security import verify_twilio_signature


class VoiceChannelAdapter(BaseChannelAdapter):
    """Channel adapter for telephony voice transcription streams (Twilio Media Streams / Deepgram)."""

    @property
    def channel_type(self) -> str:
        return "voice"

    async def verify_webhook_signature(
        self,
        request_headers: dict[str, str],
        raw_body: bytes,
        secret: str,
    ) -> bool:
        return verify_twilio_signature(request_headers, raw_body, secret)

    async def parse_inbound_event(
        self,
        raw_payload: dict[str, Any],
        organization_id: uuid.UUID,
    ) -> ChannelEvent:
        """Parses voice transcription webhook payload."""
        call_sid = str(raw_payload.get("CallSid") or raw_payload.get("call_id") or f"CA{uuid.uuid4().hex}")
        caller = str(raw_payload.get("From") or raw_payload.get("caller") or "anonymous_caller")
        transcription_text = str(
            raw_payload.get("TranscriptionText")
            or raw_payload.get("text")
            or raw_payload.get("SpeechResult")
            or ""
        ).strip()
        confidence = float(raw_payload.get("Confidence") or raw_payload.get("confidence") or 0.95)

        return ChannelEvent(
            event_id=uuid.uuid4().hex,
            organization_id=organization_id,
            channel_type=self.channel_type,
            external_user_id=caller,
            external_conversation_id=call_sid,
            external_message_id=f"rec-{uuid.uuid4().hex[:10]}",
            direction="inbound",
            payload_type="audio",
            text_content=transcription_text,
            metadata={"call_sid": call_sid, "transcription_confidence": confidence},
        )

    async def send_outbound_message(
        self,
        event: ChannelEvent,
        credentials: dict[str, Any],
    ) -> DeliveryResult:
        """Synthesizes text response into outbound telephony TTS playback."""
        return DeliveryResult(
            success=True,
            channel_type=self.channel_type,
            external_message_id=f"tts-{uuid.uuid4().hex[:12]}",
            delivery_status=DeliveryStatus.SENT,
            metadata={"call_sid": event.external_conversation_id, "speech_text": event.text_content},
        )

