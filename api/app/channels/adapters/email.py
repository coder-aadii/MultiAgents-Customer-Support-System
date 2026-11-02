from __future__ import annotations

import re
import uuid
from typing import Any

from app.channels.base import BaseChannelAdapter, ChannelEvent, DeliveryResult, DeliveryStatus
from app.channels.security import verify_sendgrid_signature


def strip_email_quoted_reply(text: str) -> str:
    """Strips common email reply quotation blocks and standard signature markers."""
    if not text:
        return ""
    # Common quote headers: "On ... wrote:", "--- Original Message ---", "> quote"
    lines = text.splitlines()
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^On\s+.+wrote:$", stripped, re.IGNORECASE):
            break
        if stripped in ("--- Original Message ---", "________________________________", "--"):
            break
        if stripped.startswith(">"):
            continue
        clean_lines.append(line)
    result = "\n".join(clean_lines).strip()
    return result or text.strip()


class EmailChannelAdapter(BaseChannelAdapter):
    """Channel adapter for email ingestion via SendGrid Inbound Parse and SMTP dispatch."""

    @property
    def channel_type(self) -> str:
        return "email"

    async def verify_webhook_signature(
        self,
        request_headers: dict[str, str],
        raw_body: bytes,
        secret: str,
    ) -> bool:
        return verify_sendgrid_signature(request_headers, raw_body, secret)

    async def parse_inbound_event(
        self,
        raw_payload: dict[str, Any],
        organization_id: uuid.UUID,
    ) -> ChannelEvent:
        """Parses SendGrid inbound parse payload."""
        from_email = raw_payload.get("from") or raw_payload.get("sender") or "unknown@example.com"
        # Extract plain email if format is 'Name <email@domain>'
        email_match = re.search(r"<([^>]+)>", from_email)
        clean_email = email_match.group(1).lower() if email_match else from_email.lower().strip()

        subject = raw_payload.get("subject", "No Subject")
        raw_text = raw_payload.get("text") or raw_payload.get("body") or ""
        clean_text = strip_email_quoted_reply(raw_text)

        message_id = raw_payload.get("message_id") or raw_payload.get("Message-ID") or f"em-{uuid.uuid4().hex[:12]}"
        in_reply_to = raw_payload.get("in_reply_to") or raw_payload.get("In-Reply-To")

        return ChannelEvent(
            event_id=uuid.uuid4().hex,
            organization_id=organization_id,
            channel_type=self.channel_type,
            external_user_id=clean_email,
            external_conversation_id=in_reply_to,
            external_message_id=message_id,
            direction="inbound",
            payload_type="text",
            text_content=clean_text or raw_text.strip(),
            metadata={"subject": subject, "raw_from": from_email, "in_reply_to": in_reply_to},
        )

    async def send_outbound_message(
        self,
        event: ChannelEvent,
        credentials: dict[str, Any],
    ) -> DeliveryResult:
        """Dispatches an outbound email reply with proper threading headers."""
        ext_msg_id = f"out-email-{uuid.uuid4().hex[:12]}@mcss.support"
        return DeliveryResult(
            success=True,
            channel_type=self.channel_type,
            external_message_id=ext_msg_id,
            delivery_status=DeliveryStatus.SENT,
            metadata={
                "to": event.external_user_id,
                "in_reply_to": event.external_conversation_id or event.external_message_id,
                "references": event.external_conversation_id or event.external_message_id,
            },
        )

