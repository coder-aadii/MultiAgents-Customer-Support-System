from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.adapters.email import EmailChannelAdapter
from app.channels.adapters.sms import SMSChannelAdapter
from app.channels.adapters.social import SocialChannelAdapter
from app.channels.adapters.voice import VoiceChannelAdapter
from app.channels.adapters.whatsapp import WhatsAppChannelAdapter
from app.channels.base import BaseChannelAdapter, ChannelEvent, DeliveryResult, DeliveryStatus
from app.channels.identity import ChannelIdentityResolver
from app.channels.security import WebhookDeduplicationGate
from app.common.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.config import settings
from app.models.channel_configuration import ChannelConfiguration
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, SenderType
from app.models.organization import Organization
from app.services.assistant_service import is_ai_handling_allowed
from app.services.job_worker import enqueue_job


class ChannelRegistry:
    """Central registry and lifecycle dispatcher for omnichannel adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseChannelAdapter] = {
            "email": EmailChannelAdapter(),
            "whatsapp": WhatsAppChannelAdapter(),
            "sms": SMSChannelAdapter(),
            "social": SocialChannelAdapter(),
            "voice": VoiceChannelAdapter(),
        }

    def get_adapter(self, channel_type: str) -> BaseChannelAdapter | None:
        return self._adapters.get(channel_type)

    def register_adapter(self, adapter: BaseChannelAdapter) -> None:
        self._adapters[adapter.channel_type] = adapter

    async def handle_inbound_webhook(
        self,
        db: AsyncSession,
        channel_type: str,
        org_slug: str,
        headers: dict[str, str],
        raw_body: bytes,
        parsed_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validates signature, prevents replays, normalizes event, links identity, and persists message."""
        adapter = self.get_adapter(channel_type)
        if not adapter:
            raise NotFoundError(f"Unsupported channel type '{channel_type}'")

        # 1. Resolve Organization
        stmt = select(Organization).where(Organization.slug == org_slug, Organization.is_active.is_(True))
        res = await db.execute(stmt)
        org = res.scalar_one_or_none()
        if not org:
            raise NotFoundError(f"Organization '{org_slug}' not found")

        # 2. Fetch Channel Configuration
        cfg_stmt = select(ChannelConfiguration).where(
            ChannelConfiguration.organization_id == org.id,
            ChannelConfiguration.channel_type == channel_type,
            ChannelConfiguration.is_enabled.is_(True),
        )
        cfg_res = await db.execute(cfg_stmt)
        config = cfg_res.scalar_one_or_none()

        # If configuration exists, enforce cryptographic signature check
        if config and config.webhook_secret_enc:
            is_valid = await adapter.verify_webhook_signature(headers, raw_body, config.webhook_secret_enc)
            if not is_valid:
                raise UnauthorizedError(f"Invalid {channel_type} webhook cryptographic signature")

        # 3. Parse JSON payload
        payload = parsed_json or {}
        if not payload and raw_body:
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except Exception:
                payload = {}

        # 4. Parse canonical ChannelEvent
        event = await adapter.parse_inbound_event(payload, org.id)

        # 5. Check replay attack deduplication gate
        dedupe_key = f"{org.id}:{channel_type}:{event.external_message_id}"
        if WebhookDeduplicationGate.is_duplicate(dedupe_key):
            return {"status": "ignored", "reason": "duplicate_event", "event_id": event.event_id}

        # 6. Resolve customer identity and active conversation
        identity, conversation, _ = await ChannelIdentityResolver.resolve_or_create(
            db=db,
            organization_id=org.id,
            channel_type=channel_type,
            external_user_id=event.external_user_id,
            profile_metadata=event.metadata,
        )

        # 7. Persist inbound message
        new_msg = Message(
            organization_id=org.id,
            conversation_id=conversation.id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content=event.text_content,
            channel_type=channel_type,
            external_message_id=event.external_message_id,
            delivery_status=DeliveryStatus.DELIVERED.value,
            metadata_json={"event_id": event.event_id, **event.metadata},
        )
        db.add(new_msg)
        await db.flush()

        # 8. Enqueue assistant background job for processing (only if open and not handed over)
        job_id_to_run = None
        if settings.MCSS_ASSISTANT_ENABLED and await is_ai_handling_allowed(db, conversation.id):
            job = await enqueue_job(db, conversation_id=conversation.id, message_id=new_msg.id)
            job_id_to_run = job.id

        await db.commit()

        if job_id_to_run:
            from app.services.job_worker import run_worker_cycle
            asyncio.create_task(run_worker_cycle(worker_id="omnichannel_bg_worker", job_id=job_id_to_run))
        return {
            "status": "success",
            "conversation_id": str(conversation.id),
            "message_id": str(new_msg.id),
            "event_id": event.event_id,
        }

    async def dispatch_outbound(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        message: Message,
    ) -> DeliveryResult | None:
        """Dispatches an outbound message if the conversation is linked to a non-widget channel."""
        from app.models.channel_identity import ChannelIdentity

        conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
        conv_res = await db.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()
        if not conv:
            return None

        # Look up channel identity for visitor
        ident_stmt = select(ChannelIdentity).where(ChannelIdentity.visitor_id == conv.visitor_id)
        ident_res = await db.execute(ident_stmt)
        identity = ident_res.scalar_one_or_none()

        if not identity or identity.channel_type == "widget":
            return None

        adapter = self.get_adapter(identity.channel_type)
        if not adapter:
            return None

        # Fetch config credentials
        cfg_stmt = select(ChannelConfiguration).where(
            ChannelConfiguration.organization_id == conv.organization_id,
            ChannelConfiguration.channel_type == identity.channel_type,
        )
        cfg_res = await db.execute(cfg_stmt)
        config = cfg_res.scalar_one_or_none()
        credentials = config.api_credentials_json if config else {}

        event = ChannelEvent(
            organization_id=conv.organization_id,
            channel_type=identity.channel_type,
            external_user_id=identity.external_user_id,
            external_conversation_id=str(conv.id),
            external_message_id=str(message.id),
            direction="outbound",
            payload_type="text",
            text_content=message.content,
        )

        result = await adapter.send_outbound_message(event, credentials)
        message.delivery_status = result.delivery_status.value
        message.external_message_id = result.external_message_id
        if result.error_code:
            message.delivery_error_code = result.error_code
        await db.commit()
        return result


default_channel_registry = ChannelRegistry()

