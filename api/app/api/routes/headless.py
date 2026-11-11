from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.rate_limit import rate_limit_visitor_message
from app.config import settings
from app.database import async_session_factory, get_session
from app.dependencies import get_visitor_session
from app.models.audit_event import ActorType, AuditEvent
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, SenderType
from app.models.organization import Organization
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.schemas.headless import (
    CustomerSessionExchangeRequest,
    CustomerSessionExchangeResponse,
    HeadlessConversationResponse,
    HeadlessMessageCreate,
)
from app.schemas.message import MessageResponse
from app.security.customer_auth import CustomerAuthError, CustomerTokenVerifier
from app.security.tokens import hash_token
from app.services.assistant_service import handle_assistant_pipeline
from app.services.conversation_service import list_messages, post_message
from app.services.job_worker import enqueue_job, multi_agent_workflow_enabled
from app.services.multi_agent_service import handle_multi_agent_pipeline
from app.tenancy.context import TenantContext
from app.tenancy.filter import TenantQueryScoper
from app.tenancy.resolver import resolve_tenant

headless_router = APIRouter(tags=["headless"])


@headless_router.post(
    "/api/v2/auth/customer-session-exchange",
    response_model=CustomerSessionExchangeResponse,
    status_code=status.HTTP_200_OK,
)
async def customer_session_exchange(
    payload: CustomerSessionExchangeRequest,
    request: Request,
    org: Organization = Depends(resolve_tenant),
    db: AsyncSession = Depends(get_session),
) -> CustomerSessionExchangeResponse:
    """Exchange customer identity tokens (SAML/OIDC/JWT) for a scoped headless session token.

    Elevates assurance level from IAL 0 (anonymous) to IAL 2 (cryptographically verified).
    """
    request_id = getattr(request.state, "request_id", None)
    customer_id: str | None = None
    email: str | None = None
    token_hash: str | None = None
    ial = 0

    if payload.customer_identity_token:
        try:
            verifier = CustomerTokenVerifier()
            verified_identity = verifier.verify_token(payload.customer_identity_token)

            if verified_identity.organization_id and verified_identity.organization_id != str(org.id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Customer identity token was issued for a different organization",
                )

            customer_id = verified_identity.customer_id
            email = verified_identity.email
            token_hash = verified_identity.token_hash
            ial = 2
        except CustomerAuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Customer identity verification failed: {exc}",
            ) from exc

    # Find or create Visitor
    visitor: Visitor | None = None
    if email:
        stmt = select(Visitor).where(
            Visitor.organization_id == org.id,
            Visitor.email == email,
        )
        stmt = TenantQueryScoper.apply_scope(stmt, Visitor)
        res = await db.execute(stmt)
        visitor = res.scalars().first()

    if not visitor:
        visitor = Visitor(
            id=uuid.uuid4(),
            organization_id=org.id,
            email=email,
            name=customer_id or f"Headless-{payload.channel}-{secrets.token_hex(4)}",
            created_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        db.add(visitor)
        await db.flush()

    # Find or create active Conversation
    conv_stmt = select(Conversation).where(
        Conversation.visitor_id == visitor.id,
        Conversation.organization_id == org.id,
        Conversation.status.in_([ConversationStatus.open, ConversationStatus.waiting_for_staff]),
    ).order_by(Conversation.created_at.desc()).limit(1)
    conv_stmt = TenantQueryScoper.apply_scope(conv_stmt, Conversation)
    res_conv = await db.execute(conv_stmt)
    conversation = res_conv.scalars().first()

    if not conversation:
        conversation = Conversation(
            id=uuid.uuid4(),
            organization_id=org.id,
            visitor_id=visitor.id,
            status=ConversationStatus.open,
            title=f"Headless Support ({payload.channel})",
            created_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(conversation)
        await db.flush()

    # Issue session token
    raw_session_token = f"mcss_sess_{secrets.token_urlsafe(32)}"
    session_hash = hash_token(raw_session_token)
    expires_at = datetime.now(UTC) + timedelta(hours=24)
    client_origin = request.headers.get("origin") or f"headless://{payload.channel}"

    visitor_session = VisitorSession(
        id=uuid.uuid4(),
        organization_id=org.id,
        visitor_id=visitor.id,
        conversation_id=conversation.id,
        token_hash=session_hash,
        origin=client_origin,
        expires_at=expires_at,
        identity_assurance_level=ial,
        verified_customer_id=customer_id,
        customer_token_hash=token_hash,
        customer_token_expires_at=expires_at if ial == 2 else None,
        created_at=datetime.now(UTC),
    )
    db.add(visitor_session)

    db.add(
        AuditEvent(
            actor_type=ActorType.visitor,
            actor_id=visitor.id,
            event_type="customer.session_exchange",
            resource_type="visitor_session",
            resource_id=visitor_session.id,
            organization_id=org.id,
            metadata_json={
                "channel": payload.channel,
                "device_fingerprint": payload.device_fingerprint,
                "ial": ial,
                "customer_id": customer_id,
                "request_id": request_id,
            },
        )
    )

    await db.commit()

    return CustomerSessionExchangeResponse(
        session_token=raw_session_token,
        expires_at=expires_at,
        identity_assurance_level=ial,
        verified_customer_id=customer_id,
        active_conversation_id=str(conversation.id),
    )


@headless_router.post(
    "/api/v2/headless/conversations/{conversation_id}/messages/stream",
    response_class=StreamingResponse,
)
async def stream_headless_message(
    conversation_id: uuid.UUID,
    payload: HeadlessMessageCreate,
    session: VisitorSession = Depends(get_visitor_session),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Send a message to a headless conversation and stream real-time tokens via SSE."""
    if session.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    rate_limit_visitor_message(session)

    # 1. Post visitor message
    msg, _ = await post_message(
        db=db,
        conversation_id=conversation_id,
        content=payload.content,
        client_message_id=payload.client_message_id,
        sender_type=SenderType.visitor,
        sender_user_id=None,
    )

    job = await enqueue_job(
        db=db,
        conversation_id=conversation_id,
        message_id=msg.id,
    )
    await db.commit()

    session_org_id = session.organization_id
    job_id = job.id
    visitor_text = payload.content

    async def sse_stream() -> AsyncGenerator[str, None]:
        # Yield message received confirmation
        yield (
            f"event: message_received\n"
            f"data: {json.dumps({'message_id': str(msg.id), 'content': msg.content})}\n\n"
        )
        yield (
            f"event: planning\n"
            f"data: {json.dumps({'status': 'analyzing_intent', 'conversation_id': str(conversation_id)})}\n\n"
        )

        try:
            async with async_session_factory() as session_db:
                TenantContext.set_tenant(session_org_id)
                if multi_agent_workflow_enabled():
                    result_msg = await handle_multi_agent_pipeline(
                        db=session_db,
                        job_id=job_id,
                        conversation_id=conversation_id,
                        visitor_message_content=visitor_text,
                    )
                else:
                    result_msg = await handle_assistant_pipeline(
                        db=session_db,
                        conversation_id=conversation_id,
                        visitor_message_content=visitor_text,
                    )

            if result_msg and result_msg.content:
                reply_content = result_msg.content
                words = reply_content.split(" ")
                for i, word in enumerate(words):
                    chunk = word if i == 0 else " " + word
                    yield f"event: token\ndata: {json.dumps({'delta': chunk})}\n\n"
                    await asyncio.sleep(0.015)

                yield (
                    f"event: completed\n"
                    f"data: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(result_msg.id), 'reply': reply_content, 'status': 'completed'})}\n\n"
                )
            else:
                yield (
                    f"event: completed\n"
                    f"data: {json.dumps({'conversation_id': str(conversation_id), 'status': 'no_response'})}\n\n"
                )

        except Exception as exc:
            yield (
                f"event: error\n"
                f"data: {json.dumps({'conversation_id': str(conversation_id), 'error': str(exc)})}\n\n"
            )

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@headless_router.get(
    "/api/v2/headless/conversations/{conversation_id}",
    response_model=HeadlessConversationResponse,
)
async def get_headless_conversation(
    conversation_id: uuid.UUID,
    session: VisitorSession = Depends(get_visitor_session),
    db: AsyncSession = Depends(get_session),
) -> HeadlessConversationResponse:
    """Get metadata for a headless conversation."""
    if session.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.organization_id == session.organization_id,
    )
    stmt = TenantQueryScoper.apply_scope(stmt, Conversation)
    res = await db.execute(stmt)
    conv = res.scalars().first()
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    return HeadlessConversationResponse(
        id=conv.id,
        status=conv.status.value,
        title=conv.title,
        created_at=conv.created_at,
        last_message_at=conv.last_message_at,
    )


@headless_router.get(
    "/api/v2/headless/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
async def get_headless_messages(
    conversation_id: uuid.UUID,
    session: VisitorSession = Depends(get_visitor_session),
    db: AsyncSession = Depends(get_session),
) -> list[MessageResponse]:
    """Retrieve message history for a headless conversation."""
    if session.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    messages_page = await list_messages(db, conversation_id=conversation_id, limit=50)
    return messages_page.items
