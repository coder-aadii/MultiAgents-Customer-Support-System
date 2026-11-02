from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, asc, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.cursor import decode_cursor, encode_cursor
from app.common.errors import ConversationClosedError, NotFoundError
from app.models.audit_event import ActorType, AuditEvent
from app.models.conversation import Conversation, ConversationStatus
from app.models.handoff import Handoff, HandoffStatus
from app.models.message import Message, SenderType
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.conversation import ConversationDetailResponse, ConversationResponse
from app.schemas.handoff import HandoffResponse
from app.schemas.message import MessageResponse
from app.tenancy.context import TenantContext
from app.tenancy.filter import TenantQueryScoper


async def record_audit(
    db: AsyncSession,
    actor_type: ActorType,
    actor_id: uuid.UUID | None,
    event_type: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    audit = AuditEvent(
        actor_type=actor_type,
        actor_id=actor_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=metadata or {},
    )
    db.add(audit)
    await db.flush()


async def list_conversations(
    db: AsyncSession,
    status: ConversationStatus | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> PaginatedResponse[ConversationResponse]:
    query = select(Conversation).options(
        selectinload(Conversation.assignee),
        selectinload(Conversation.visitor),
    )
    query = TenantQueryScoper.apply_scope(query, Conversation)

    if status:
        query = query.where(Conversation.status == status)

    if cursor:
        dt, item_id = decode_cursor(cursor)
        query = query.where(
            or_(
                Conversation.last_message_at < dt,
                and_(Conversation.last_message_at == dt, Conversation.id < item_id),
            )
        )

    query = query.order_by(desc(Conversation.last_message_at), desc(Conversation.id)).limit(
        limit + 1
    )
    result = await db.execute(query)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(last.last_message_at, last.id)

    return PaginatedResponse(
        items=[ConversationResponse.model_validate(c) for c in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def get_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
) -> ConversationDetailResponse:
    query = (
        select(Conversation)
        .options(
            selectinload(Conversation.assignee),
            selectinload(Conversation.visitor),
        )
        .where(Conversation.id == conversation_id)
    )
    query = TenantQueryScoper.apply_scope(query, Conversation)
    result = await db.execute(query)
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Conversation not found")
    return ConversationDetailResponse.model_validate(conv)


async def claim_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    staff_user: User,
    request_id: str | None = None,
) -> ConversationResponse:
    query = (
        select(Conversation)
        .options(
            selectinload(Conversation.assignee),
            selectinload(Conversation.visitor),
        )
        .where(Conversation.id == conversation_id)
        .with_for_update()
    )
    query = TenantQueryScoper.apply_scope(query, Conversation)
    result = await db.execute(query)
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Conversation not found")

    if conv.status == ConversationStatus.closed:
        raise ConversationClosedError("Cannot claim a closed conversation")

    now = datetime.now(UTC)
    conv.assigned_user_id = staff_user.id
    conv.status = ConversationStatus.staff_active
    conv.updated_at = now

    # Also claim any queued handoff for this conversation
    handoff_stmt = (
        select(Handoff)
        .where(Handoff.conversation_id == conversation_id, Handoff.status == HandoffStatus.queued)
        .with_for_update()
    )
    handoff_res = await db.execute(handoff_stmt)
    handoff = handoff_res.scalar_one_or_none()
    if handoff:
        handoff.status = HandoffStatus.claimed
        handoff.claimed_by_user_id = staff_user.id
        handoff.claimed_at = now

    await record_audit(
        db,
        actor_type=ActorType.user,
        actor_id=staff_user.id,
        event_type="conversation.claim",
        resource_type="conversation",
        resource_id=conv.id,
        metadata={"assigned_user_id": str(staff_user.id), "request_id": request_id},
    )
    await db.commit()
    await db.refresh(conv)
    return ConversationResponse.model_validate(conv)


async def close_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    actor_user: User | None = None,
    request_id: str | None = None,
) -> ConversationResponse:
    query = (
        select(Conversation)
        .options(
            selectinload(Conversation.assignee),
            selectinload(Conversation.visitor),
        )
        .where(Conversation.id == conversation_id)
        .with_for_update()
    )
    query = TenantQueryScoper.apply_scope(query, Conversation)
    result = await db.execute(query)
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Conversation not found")

    now = datetime.now(UTC)
    if conv.status != ConversationStatus.closed:
        conv.status = ConversationStatus.closed
        conv.updated_at = now

        # Resolve any active handoff
        handoff_stmt = (
            select(Handoff)
            .where(
                Handoff.conversation_id == conversation_id,
                Handoff.status.in_([HandoffStatus.queued, HandoffStatus.claimed]),
            )
            .with_for_update()
        )
        handoff_res = await db.execute(handoff_stmt)
        handoff = handoff_res.scalar_one_or_none()
        if handoff:
            handoff.status = HandoffStatus.resolved
            handoff.resolved_at = now

        await record_audit(
            db,
            actor_type=ActorType.user if actor_user else ActorType.system,
            actor_id=actor_user.id if actor_user else None,
            event_type="conversation.close",
            resource_type="conversation",
            resource_id=conv.id,
            metadata={"request_id": request_id},
        )
        await db.commit()
        await db.refresh(conv)

    return ConversationResponse.model_validate(conv)


async def post_message(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    content: str,
    client_message_id: uuid.UUID,
    sender_type: SenderType,
    sender_user_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> tuple[MessageResponse, bool]:
    """Atomically create a message with idempotency on (conversation_id, client_message_id)."""
    # 1. Check conversation status with lock
    query = select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    query = TenantQueryScoper.apply_scope(query, Conversation)
    result = await db.execute(query)
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Conversation not found")

    if conv.status == ConversationStatus.closed:
        raise ConversationClosedError("Cannot post message to a closed conversation")

    # 2. Check for existing idempotent message
    existing_stmt = select(Message).where(
        Message.conversation_id == conversation_id,
        Message.client_message_id == client_message_id,
    )
    existing_res = await db.execute(existing_stmt)
    existing_msg = existing_res.scalar_one_or_none()
    if existing_msg:
        return MessageResponse.model_validate(existing_msg), False

    # 3. Create message & atomically update conversation timestamp
    now = datetime.now(UTC)
    new_message = Message(
        conversation_id=conversation_id,
        client_message_id=client_message_id,
        sender_type=sender_type,
        sender_user_id=sender_user_id,
        content=content,
        created_at=now,
    )
    db.add(new_message)

    conv.last_message_at = now
    conv.updated_at = now

    await record_audit(
        db,
        actor_type=ActorType.user if sender_user_id else ActorType.visitor,
        actor_id=sender_user_id,
        event_type="message.create",
        resource_type="message",
        resource_id=new_message.id,
        metadata={"conversation_id": str(conversation_id), "request_id": request_id},
    )
    await db.commit()
    await db.refresh(new_message)

    # Dispatch outbound message to external channel if applicable
    if sender_type in (SenderType.staff, SenderType.assistant):
        try:
            from app.channels.registry import default_channel_registry
            await default_channel_registry.dispatch_outbound(db, conversation_id, new_message)
        except Exception as e:
            logger.warning("Failed outbound channel dispatch", error=str(e), conversation_id=str(conversation_id))

    return MessageResponse.model_validate(new_message), True


async def list_messages(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = 50,
) -> PaginatedResponse[MessageResponse]:
    query = select(Message).where(Message.conversation_id == conversation_id)
    query = TenantQueryScoper.apply_scope(query, Message)

    if cursor:
        dt, item_id = decode_cursor(cursor)
        query = query.where(
            or_(
                Message.created_at > dt,
                and_(Message.created_at == dt, Message.id > item_id),
            )
        )

    # Messages returned in stable ascending order
    query = query.order_by(asc(Message.created_at), asc(Message.id)).limit(limit + 1)
    result = await db.execute(query)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if items:
        last = items[-1]
        next_cursor = encode_cursor(last.created_at, last.id)

    return PaginatedResponse(
        items=[MessageResponse.model_validate(m) for m in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_handoffs(
    db: AsyncSession,
    status: HandoffStatus = HandoffStatus.queued,
    cursor: str | None = None,
    limit: int = 25,
) -> PaginatedResponse[HandoffResponse]:
    query = (
        select(Handoff)
        .join(Conversation, Conversation.id == Handoff.conversation_id)
        .where(Handoff.status == status)
    )
    query = TenantQueryScoper.apply_scope(query, Conversation)

    if cursor:
        dt, item_id = decode_cursor(cursor)
        query = query.where(
            or_(
                Handoff.created_at < dt,
                and_(Handoff.created_at == dt, Handoff.id < item_id),
            )
        )

    query = query.order_by(desc(Handoff.created_at), desc(Handoff.id)).limit(limit + 1)
    result = await db.execute(query)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if items:
        last = items[-1]
        next_cursor = encode_cursor(last.created_at, last.id)

    return PaginatedResponse(
        items=[HandoffResponse.model_validate(h) for h in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def claim_handoff(
    db: AsyncSession,
    handoff_id: uuid.UUID,
    staff_user: User,
    request_id: str | None = None,
) -> HandoffResponse:
    query = (
        select(Handoff)
        .join(Conversation, Conversation.id == Handoff.conversation_id)
        .where(Handoff.id == handoff_id)
        .with_for_update()
    )
    query = TenantQueryScoper.apply_scope(query, Conversation)
    result = await db.execute(query)
    handoff = result.scalar_one_or_none()
    if not handoff:
        raise NotFoundError("Handoff not found")

    now = datetime.now(UTC)
    handoff.status = HandoffStatus.claimed
    handoff.claimed_by_user_id = staff_user.id
    handoff.claimed_at = now

    # Also update associated conversation
    conv_stmt = (
        select(Conversation).where(Conversation.id == handoff.conversation_id).with_for_update()
    )
    conv_res = await db.execute(conv_stmt)
    conv = conv_res.scalar_one_or_none()
    if conv and conv.status != ConversationStatus.closed:
        conv.assigned_user_id = staff_user.id
        conv.status = ConversationStatus.staff_active
        conv.updated_at = now

    await record_audit(
        db,
        actor_type=ActorType.user,
        actor_id=staff_user.id,
        event_type="handoff.claim",
        resource_type="handoff",
        resource_id=handoff.id,
        metadata={"request_id": request_id},
    )
    await db.commit()
    await db.refresh(handoff)
    return HandoffResponse.model_validate(handoff)
