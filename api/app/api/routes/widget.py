from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.rate_limit import rate_limit_visitor_message, rate_limit_widget_session
from app.config import settings
from app.core.context import OrganizationContext, get_organization_context
from app.database import get_session
from app.dependencies import get_visitor_session, validate_visitor_conversation
from app.models.message import SenderType
from app.models.visitor_session import VisitorSession
from app.schemas.common import PaginatedResponse
from app.schemas.conversation import ConversationDetailResponse
from app.schemas.message import MessageCreate, MessageResponse
from app.schemas.visitor import (
    VisitorProfileUpdate,
    VisitorResponse,
    WidgetSessionRequest,
    WidgetSessionResponse,
)
from app.services.assistant_service import is_ai_handling_allowed
from app.services.conversation_service import get_conversation, list_messages, post_message
from app.services.job_worker import enqueue_job, run_worker_cycle
from app.services.visitor_service import (
    create_or_resume_session,
    invalidate_visitor_session,
    update_visitor_profile,
)
from app.tenancy.resolver import resolve_tenant

widget_router = APIRouter(prefix="/widget", tags=["widget"])


@widget_router.post("/sessions", response_model=WidgetSessionResponse)
async def create_session(
    request: Request,
    payload: WidgetSessionRequest | None = None,
    authorization: str | None = Header(default=None),
    x_customer_token: str | None = Header(default=None, alias="x-customer-token"),
    origin: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
    org = Depends(resolve_tenant),
) -> WidgetSessionResponse:
    request_id = getattr(request.state, "request_id", None)
    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization.split(" ", 1)[1].strip()

    name = payload.name if payload else None
    email = payload.email if payload else None
    customer_token = x_customer_token or (payload.customer_token if payload else None)

    # Apply rate limiting for new session creation (resuming with valid token is not counted)
    if not raw_token:
        rate_limit_widget_session(request, origin)

    # Browser supplies Origin header automatically
    return await create_or_resume_session(
        db=db,
        origin=origin,
        raw_token=raw_token,
        customer_token=customer_token,
        name=name,
        email=email,
        request_id=request_id,
    )


@widget_router.post("/sessions/logout")
async def logout_session(
    request: Request,
    session: VisitorSession = Depends(get_visitor_session),
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    request_id = getattr(request.state, "request_id", None)
    await invalidate_visitor_session(db=db, session=session, request_id=request_id)
    return {"status": "ok", "message": "Session invalidated"}


@widget_router.get("/conversation", response_model=ConversationDetailResponse)
async def get_visitor_current_conversation(
    session: VisitorSession = Depends(get_visitor_session),
    db: AsyncSession = Depends(get_session),
) -> ConversationDetailResponse:
    return await get_conversation(db, conversation_id=session.conversation_id)


@widget_router.get("/messages", response_model=PaginatedResponse[MessageResponse])
async def get_visitor_current_messages(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    session: VisitorSession = Depends(get_visitor_session),
    db: AsyncSession = Depends(get_session),
) -> PaginatedResponse[MessageResponse]:
    return await list_messages(
        db, conversation_id=session.conversation_id, cursor=cursor, limit=limit
    )


@widget_router.post(
    "/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_visitor_current_message(
    payload: MessageCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    session: VisitorSession = Depends(get_visitor_session),
    db: AsyncSession = Depends(get_session),
) -> MessageResponse:
    request_id = getattr(request.state, "request_id", None)
    rate_limit_visitor_message(session)
    msg, created = await post_message(
        db=db,
        conversation_id=session.conversation_id,
        content=payload.content,
        client_message_id=payload.client_message_id,
        sender_type=SenderType.visitor,
        sender_user_id=None,
        request_id=request_id,
    )
    if created and settings.MCSS_ASSISTANT_ENABLED and await is_ai_handling_allowed(db, session.conversation_id):
        job = await enqueue_job(
            db=db,
            conversation_id=session.conversation_id,
            message_id=msg.id,
        )
        await db.commit()
        background_tasks.add_task(run_worker_cycle, worker_id="widget_bg_worker", job_id=job.id)
    return msg


@widget_router.patch("/profile", response_model=VisitorResponse)
async def update_visitor_profile_endpoint(
    payload: VisitorProfileUpdate,
    request: Request,
    session: VisitorSession = Depends(get_visitor_session),
    db: AsyncSession = Depends(get_session),
) -> VisitorResponse:
    request_id = getattr(request.state, "request_id", None)
    visitor = await update_visitor_profile(
        db=db,
        visitor_id=session.visitor_id,
        name=payload.name,
        email=payload.email,
        request_id=request_id,
    )
    return VisitorResponse.model_validate(visitor)


@widget_router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_visitor_conversation(
    conversation_id: uuid.UUID,
    session: VisitorSession = Depends(validate_visitor_conversation),
    db: AsyncSession = Depends(get_session),
) -> ConversationDetailResponse:
    return await get_conversation(db, conversation_id=conversation_id)


@widget_router.get(
    "/conversations/{conversation_id}/messages", response_model=PaginatedResponse[MessageResponse]
)
async def get_visitor_messages(
    conversation_id: uuid.UUID,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    session: VisitorSession = Depends(validate_visitor_conversation),
    db: AsyncSession = Depends(get_session),
) -> PaginatedResponse[MessageResponse]:
    return await list_messages(db, conversation_id=conversation_id, cursor=cursor, limit=limit)


@widget_router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_visitor_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    session: VisitorSession = Depends(validate_visitor_conversation),
    db: AsyncSession = Depends(get_session),
) -> MessageResponse:
    request_id = getattr(request.state, "request_id", None)
    rate_limit_visitor_message(session)
    msg, created = await post_message(
        db=db,
        conversation_id=conversation_id,
        content=payload.content,
        client_message_id=payload.client_message_id,
        sender_type=SenderType.visitor,
        sender_user_id=None,
        request_id=request_id,
    )
    if created and settings.MCSS_ASSISTANT_ENABLED and await is_ai_handling_allowed(db, conversation_id):
        job = await enqueue_job(
            db=db,
            conversation_id=conversation_id,
            message_id=msg.id,
        )
        await db.commit()
        background_tasks.add_task(run_worker_cycle, worker_id="widget_bg_worker", job_id=job.id)
    return msg
