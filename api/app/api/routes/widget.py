from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.dependencies import validate_visitor_conversation
from app.models.message import SenderType
from app.models.visitor_session import VisitorSession
from app.schemas.common import PaginatedResponse
from app.schemas.conversation import ConversationDetailResponse
from app.schemas.message import MessageCreate, MessageResponse
from app.schemas.visitor import WidgetSessionRequest, WidgetSessionResponse
from app.services.conversation_service import get_conversation, list_messages, post_message
from app.services.visitor_service import create_or_resume_session

widget_router = APIRouter(prefix="/widget", tags=["widget"])


@widget_router.post("/sessions", response_model=WidgetSessionResponse)
async def create_session(
    request: Request,
    payload: WidgetSessionRequest | None = None,
    authorization: str | None = Header(default=None),
    origin: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> WidgetSessionResponse:
    request_id = getattr(request.state, "request_id", None)
    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization.split(" ", 1)[1].strip()

    name = payload.name if payload else None
    email = payload.email if payload else None

    # Browser supplies Origin header automatically
    return await create_or_resume_session(
        db=db,
        origin=origin,
        raw_token=raw_token,
        name=name,
        email=email,
        request_id=request_id,
    )


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
    session: VisitorSession = Depends(validate_visitor_conversation),
    db: AsyncSession = Depends(get_session),
) -> MessageResponse:
    request_id = getattr(request.state, "request_id", None)
    msg, _ = await post_message(
        db=db,
        conversation_id=conversation_id,
        content=payload.content,
        client_message_id=payload.client_message_id,
        sender_type=SenderType.visitor,
        sender_user_id=None,
        request_id=request_id,
    )
    return msg
