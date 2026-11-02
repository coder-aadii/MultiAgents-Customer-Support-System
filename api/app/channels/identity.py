from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import ChannelEvent
from app.models.channel_identity import ChannelIdentity
from app.models.conversation import Conversation, ConversationStatus
from app.models.visitor import Visitor


class ChannelIdentityResolver:
    """Resolves omnichannel identifiers (phone, email, social ID) to MCSS Visitors and Conversations."""

    @classmethod
    async def resolve_or_create(
        cls,
        db: AsyncSession,
        organization_id: uuid.UUID,
        channel_type: str,
        external_user_id: str,
        profile_metadata: dict[str, Any] | None = None,
    ) -> tuple[ChannelIdentity, Conversation, bool]:
        """Resolves existing identity or provisions new Visitor and Conversation."""
        clean_user_id = str(external_user_id).strip()
        meta = profile_metadata or {}
        now = datetime.now(UTC)

        # 1. Lookup existing channel identity
        stmt = select(ChannelIdentity).where(
            ChannelIdentity.organization_id == organization_id,
            ChannelIdentity.channel_type == channel_type,
            ChannelIdentity.external_user_id == clean_user_id,
        )
        res = await db.execute(stmt)
        channel_identity = res.scalar_one_or_none()

        is_new = False
        if not channel_identity:
            is_new = True
            # Provision visitor
            visitor_email = clean_user_id if "@" in clean_user_id else None
            visitor_name = meta.get("name") or meta.get("display_name") or f"{channel_type.capitalize()} Customer"

            visitor = Visitor(
                organization_id=organization_id,
                name=visitor_name,
                email=visitor_email,
                created_at=now,
                last_seen_at=now,
            )
            db.add(visitor)
            await db.flush()

            channel_identity = ChannelIdentity(
                organization_id=organization_id,
                channel_type=channel_type,
                external_user_id=clean_user_id,
                customer_id=meta.get("customer_id"),
                visitor_id=visitor.id,
                profile_metadata_json=meta,
                created_at=now,
            )
            db.add(channel_identity)
            await db.flush()
        else:
            visitor_res = await db.execute(select(Visitor).where(Visitor.id == channel_identity.visitor_id))
            visitor = visitor_res.scalar_one()
            visitor.last_seen_at = now
            if meta:
                channel_identity.profile_metadata_json = {**channel_identity.profile_metadata_json, **meta}
            await db.flush()

        # 2. Find active conversation or create new
        conv_stmt = (
            select(Conversation)
            .where(
                Conversation.organization_id == organization_id,
                Conversation.visitor_id == visitor.id,
                Conversation.status.in_([ConversationStatus.open, ConversationStatus.staff_active, ConversationStatus.waiting_for_staff]),
            )
            .order_by(desc(Conversation.last_message_at))
            .limit(1)
        )
        conv_res = await db.execute(conv_stmt)
        conversation = conv_res.scalar_one_or_none()

        if not conversation:
            conversation = Conversation(
                organization_id=organization_id,
                visitor_id=visitor.id,
                status=ConversationStatus.open,
                last_message_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(conversation)
            await db.flush()

        return channel_identity, conversation, is_new

