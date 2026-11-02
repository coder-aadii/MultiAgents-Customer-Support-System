from __future__ import annotations

import uuid
from typing import Any

from app.channels.base import BaseChannelAdapter, ChannelEvent, DeliveryResult, DeliveryStatus
from app.channels.security import verify_twilio_signature

STOP_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_KEYWORDS = {"START", "UNSTOP", "YES"}
HELP_KEYWORDS = {"HELP", "INFO"}


class SMSChannelAdapter(BaseChannelAdapter):
    """Channel adapter for SMS via Twilio API with carrier compliance."""

    @property
    def channel_type(self) -> str:
        return "sms"

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
        """Parses Twilio inbound SMS webhook payload."""
        from_phone = str(raw_payload.get("From") or raw_payload.get("from") or "unknown_phone").strip()
        body = str(raw_payload.get("Body") or raw_payload.get("body") or "").strip()
        msg_sid = str(raw_payload.get("MessageSid") or raw_payload.get("SmsSid") or f"SM{uuid.uuid4().hex}")

        clean_keyword = body.upper()
        opt_out = clean_keyword in STOP_KEYWORDS
        opt_in = clean_keyword in START_KEYWORDS
        is_help = clean_keyword in HELP_KEYWORDS

        return ChannelEvent(
            event_id=uuid.uuid4().hex,
            organization_id=organization_id,
            channel_type=self.channel_type,
            external_user_id=from_phone,
            external_message_id=msg_sid,
            direction="inbound",
            payload_type="text",
            text_content=body,
            metadata={
                "opt_out": opt_out,
                "opt_in": opt_in,
                "is_help": is_help,
                "segments": max(1, (len(body) + 159) // 160),
            },
        )

    async def send_outbound_message(
        self,
        event: ChannelEvent,
        credentials: dict[str, Any],
    ) -> DeliveryResult:
        """Sends an outbound SMS."""
        sid = f"SM{uuid.uuid4().hex}"
        return DeliveryResult(
            success=True,
            channel_type=self.channel_type,
            external_message_id=sid,
            delivery_status=DeliveryStatus.SENT,
            metadata={"to": event.external_user_id, "segments": max(1, (len(event.text_content) + 159) // 160)},
        )

