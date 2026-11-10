from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, asc, cast, desc, func, or_, select, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.cursor import decode_cursor, encode_cursor
from app.common.errors import ConversationClosedError, NotFoundError
from app.models.audit_event import ActorType, AuditEvent
from app.models.conversation import Conversation, ConversationStatus
from app.models.handoff import Handoff, HandoffStatus
from app.models.message import Message, SenderType
from app.models.user import User
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.schemas.common import PaginatedResponse
from app.schemas.conversation import (
    ConversationDetailResponse,
    ConversationResponse,
    CustomerHistoryResponse,
    CustomerPastConversationItem,
)
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


def _escape_like_pattern(text: str) -> str:
    """Escape SQL LIKE/ILIKE wildcards so user input is treated as literal text."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def list_conversations(
    db: AsyncSession,
    status: ConversationStatus | None = None,
    q: str | None = None,
    customer_id: str | None = None,
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

    if customer_id and customer_id.strip():
        cid = customer_id.strip()
        try:
            cust_uuid = uuid.UUID(cid)
            query = query.where(Conversation.visitor_id == cust_uuid)
        except ValueError:
            # Avoid unindexed full table scans on invalid customer UUID inputs
            query = query.where(Conversation.id.is_(None))

    if q and q.strip():
        clean_q = q.strip()
        try:
            search_uuid = uuid.UUID(clean_q)
            query = query.where(
                or_(
                    Conversation.id == search_uuid,
                    Conversation.visitor_id == search_uuid,
                )
            )
        except ValueError:
            pattern = _escape_like_pattern(clean_q)
            msg_subq = (
                select(Message.conversation_id)
                .where(Message.content.ilike(pattern))
                .distinct()
            )
            msg_subq = TenantQueryScoper.apply_scope(msg_subq, Message).scalar_subquery()

            vis_subq = select(Visitor.id).where(
                or_(
                    Visitor.name.ilike(pattern),
                    Visitor.email.ilike(pattern),
                )
            )
            vis_subq = TenantQueryScoper.apply_scope(vis_subq, Visitor).scalar_subquery()

            query = query.where(
                or_(
                    Conversation.visitor_id.in_(vis_subq),
                    Conversation.id.in_(msg_subq),
                    Conversation.title.ilike(pattern),
                )
            )

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


async def get_customer_history(
    db: AsyncSession,
    conversation_id: uuid.UUID,
) -> CustomerHistoryResponse:
    conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
    conv_stmt = TenantQueryScoper.apply_scope(conv_stmt, Conversation)
    conv = (await db.execute(conv_stmt)).scalar_one_or_none()
    if not conv:
        raise NotFoundError("Conversation not found")

    vis = (await db.execute(select(Visitor).where(Visitor.id == conv.visitor_id))).scalar_one_or_none()

    sess_stmt = select(VisitorSession).where(VisitorSession.conversation_id == conv.id)
    sess_stmt = TenantQueryScoper.apply_scope(sess_stmt, VisitorSession)
    sess = (await db.execute(sess_stmt)).scalars().first()

    customer_predicates = [Conversation.visitor_id == conv.visitor_id]
    if vis and vis.email and vis.email.strip():
        clean_email = vis.email.strip()
        same_email_subq = select(Visitor.id).where(
            Visitor.email == clean_email,
            Visitor.organization_id == conv.organization_id,
        )
        customer_predicates.append(Conversation.visitor_id.in_(same_email_subq))
    if sess and sess.verified_customer_id:
        same_customer_subq = select(VisitorSession.conversation_id).where(
            VisitorSession.verified_customer_id == sess.verified_customer_id,
            VisitorSession.organization_id == conv.organization_id,
        )
        customer_predicates.append(Conversation.id.in_(same_customer_subq))

    past_stmt = (
        select(Conversation)
        .options(selectinload(Conversation.assignee))
        .where(
            or_(*customer_predicates),
            Conversation.id != conv.id,
        )
        .order_by(desc(Conversation.created_at))
        .limit(50)
    )
    past_stmt = TenantQueryScoper.apply_scope(past_stmt, Conversation)
    past_convs = list((await db.execute(past_stmt)).scalars().all())

    if not past_convs:
        return CustomerHistoryResponse(
            visitor_id=conv.visitor_id,
            visitor_name=vis.name if vis else None,
            visitor_email=vis.email if vis else None,
            past_conversations=[],
        )

    past_ids = [pc.id for pc in past_convs]

    # Batch 1: Aggregate message counts in a single query
    count_query = (
        select(Message.conversation_id, func.count(Message.id).label("cnt"))
        .where(Message.conversation_id.in_(past_ids))
        .group_by(Message.conversation_id)
    )
    count_query = TenantQueryScoper.apply_scope(count_query, Message)
    count_rows = (await db.execute(count_query)).all()
    counts_map = {row[0]: row[1] for row in count_rows}

    # Batch 2: Fetch latest message per conversation via window function subquery
    rn_subq = (
        select(
            Message.conversation_id,
            Message.content,
            func.row_number()
            .over(
                partition_by=Message.conversation_id,
                order_by=desc(Message.created_at),
            )
            .label("rn"),
        )
        .where(Message.conversation_id.in_(past_ids))
    )
    rn_subq = TenantQueryScoper.apply_scope(rn_subq, Message).subquery()

    last_msg_query = select(rn_subq.c.conversation_id, rn_subq.c.content).where(
        rn_subq.c.rn == 1
    )
    last_msg_rows = (await db.execute(last_msg_query)).all()
    previews_map = {row[0]: row[1] for row in last_msg_rows}

    past_items: list[CustomerPastConversationItem] = []
    for pc in past_convs:
        msg_count = counts_map.get(pc.id, 0)
        last_msg = previews_map.get(pc.id)
        preview = (last_msg[:120] + "...") if last_msg and len(last_msg) > 120 else last_msg

        past_items.append(
            CustomerPastConversationItem(
                id=pc.id,
                status=pc.status,
                title=pc.title,
                created_at=pc.created_at,
                last_message_at=pc.last_message_at,
                assigned_agent_name=pc.assignee.display_name if pc.assignee else None,
                last_message_preview=preview,
                total_messages=msg_count,
            )
        )

    return CustomerHistoryResponse(
        visitor_id=conv.visitor_id,
        visitor_name=vis.name if vis else None,
        visitor_email=vis.email if vis else None,
        past_conversations=past_items,
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
