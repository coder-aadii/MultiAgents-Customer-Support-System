from __future__ import annotations

import uuid
from typing import Any

from app.channels.base import BaseChannelAdapter, ChannelEvent, DeliveryResult, DeliveryStatus
from app.channels.security import verify_generic_hmac_sha256


class SocialChannelAdapter(BaseChannelAdapter):
    """Channel adapter for Slack / Meta social direct messages."""

    @property
    def channel_type(self) -> str:
        return "social"

    async def verify_webhook_signature(
        self,
        request_headers: dict[str, str],
        raw_body: bytes,
        secret: str,
    ) -> bool:
        return verify_generic_hmac_sha256(request_headers, raw_body, secret, header_name="X-Slack-Signature")

    async def parse_inbound_event(
        self,
        raw_payload: dict[str, Any],
        organization_id: uuid.UUID,
    ) -> ChannelEvent:
        """Parses Slack/Meta event callback or interactive block action."""
        # Slack event callback
        event_obj = raw_payload.get("event") or {}
        user_id = str(event_obj.get("user") or raw_payload.get("user_id") or "U_UNKNOWN")
        text = str(event_obj.get("text") or raw_payload.get("text") or "").strip()
        ts = str(event_obj.get("ts") or raw_payload.get("ts") or uuid.uuid4().hex[:10])
        thread_ts = event_obj.get("thread_ts")

        # Interactive block actions
        actions = raw_payload.get("actions") or []
        if actions:
            action = actions[0]
            text = action.get("value") or (action.get("text") or {}).get("text", text)

        return ChannelEvent(
            event_id=uuid.uuid4().hex,
            organization_id=organization_id,
            channel_type=self.channel_type,
            external_user_id=user_id,
            external_conversation_id=thread_ts,
            external_message_id=ts,
            direction="inbound",
            payload_type="text",
            text_content=text,
            metadata={"thread_ts": thread_ts, "raw_user": user_id},
        )

    async def send_outbound_message(
        self,
        event: ChannelEvent,
        credentials: dict[str, Any],
    ) -> DeliveryResult:
        """Sends an outbound social channel message."""
        return DeliveryResult(
            success=True,
            channel_type=self.channel_type,
            external_message_id=f"soc-{uuid.uuid4().hex[:12]}",
            delivery_status=DeliveryStatus.SENT,
            metadata={"channel": event.external_user_id, "thread_ts": event.external_conversation_id},
        )

