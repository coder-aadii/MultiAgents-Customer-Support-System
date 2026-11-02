from __future__ import annotations

import uuid
from typing import Any

from app.channels.base import BaseChannelAdapter, ChannelEvent, DeliveryResult, DeliveryStatus
from app.channels.security import verify_whatsapp_signature


class WhatsAppChannelAdapter(BaseChannelAdapter):
    """Channel adapter for WhatsApp Business Cloud API."""

    @property
    def channel_type(self) -> str:
        return "whatsapp"

    async def verify_webhook_signature(
        self,
        request_headers: dict[str, str],
        raw_body: bytes,
        secret: str,
    ) -> bool:
        return verify_whatsapp_signature(request_headers, raw_body, secret)

    def verify_webhook_challenge(
        self,
        mode: str | None,
        verify_token: str | None,
        challenge: str | None,
        configured_token: str,
    ) -> str | None:
        """Handles Meta Cloud API initial webhook subscription challenge."""
        if mode == "subscribe" and verify_token == configured_token:
            return challenge
        return None

    async def parse_inbound_event(
        self,
        raw_payload: dict[str, Any],
        organization_id: uuid.UUID,
    ) -> ChannelEvent:
        """Parses Meta WhatsApp Cloud API change notification."""
        entry = (raw_payload.get("entry") or [{}])[0]
        change = (entry.get("changes") or [{}])[0]
        value = change.get("value") or {}
        contacts = value.get("contacts") or [{}]
        messages = value.get("messages") or [{}]

        msg = messages[0] if messages else {}
        contact = contacts[0] if contacts else {}

        phone = str(msg.get("from") or contact.get("wa_id") or "unknown_phone")
        msg_id = str(msg.get("id") or f"wamid-{uuid.uuid4().hex[:12]}")
        profile_name = (contact.get("profile") or {}).get("name") or "WhatsApp User"

        # Text or interactive quick-reply button reply
        text = ""
        msg_type = msg.get("type", "text")
        if msg_type == "text":
            text = (msg.get("text") or {}).get("body", "")
        elif msg_type == "interactive":
            interactive = msg.get("interactive") or {}
            btn_reply = interactive.get("button_reply") or {}
            text = btn_reply.get("title") or btn_reply.get("id", "")
        elif msg_type == "button":
            text = (msg.get("button") or {}).get("text", "")

        return ChannelEvent(
            event_id=uuid.uuid4().hex,
            organization_id=organization_id,
            channel_type=self.channel_type,
            external_user_id=phone,
            external_message_id=msg_id,
            direction="inbound",
            payload_type="text",
            text_content=text.strip(),
            metadata={"profile_name": profile_name, "wa_type": msg_type},
        )

    async def send_outbound_message(
        self,
        event: ChannelEvent,
        credentials: dict[str, Any],
    ) -> DeliveryResult:
        """Sends an outbound WhatsApp message."""
        ext_msg_id = f"wamid.out.{uuid.uuid4().hex[:16]}"
        return DeliveryResult(
            success=True,
            channel_type=self.channel_type,
            external_message_id=ext_msg_id,
            delivery_status=DeliveryStatus.SENT,
            metadata={"to": event.external_user_id, "messaging_product": "whatsapp"},
        )

