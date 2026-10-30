from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.dependencies import require_staff
from app.models.conversation import ConversationStatus
from app.models.message import SenderType
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.conversation import ConversationDetailResponse, ConversationResponse
from app.schemas.message import MessageCreate, MessageResponse
from app.services.conversation_service import (
    claim_conversation,
    close_conversation,
    get_conversation,
    list_conversations,
    list_messages,
    post_message,
)

conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])


@conversations_router.get("", response_model=PaginatedResponse[ConversationResponse])
async def get_conversations(
    status: ConversationStatus | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> PaginatedResponse[ConversationResponse]:
    return await list_conversations(db, status=status, cursor=cursor, limit=limit)


@conversations_router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation_detail(
    conversation_id: uuid.UUID,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> ConversationDetailResponse:
    return await get_conversation(db, conversation_id=conversation_id)


@conversations_router.post("/{conversation_id}/claim", response_model=ConversationResponse)
async def claim(
    conversation_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    request_id = getattr(request.state, "request_id", None)
    return await claim_conversation(
        db, conversation_id=conversation_id, staff_user=current_user, request_id=request_id
    )


@conversations_router.post("/{conversation_id}/close", response_model=ConversationResponse)
async def close(
    conversation_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    request_id = getattr(request.state, "request_id", None)
    return await close_conversation(
        db, conversation_id=conversation_id, actor_user=current_user, request_id=request_id
    )


@conversations_router.get(
    "/{conversation_id}/messages", response_model=PaginatedResponse[MessageResponse]
)
async def get_messages(
    conversation_id: uuid.UUID,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> PaginatedResponse[MessageResponse]:
    return await list_messages(db, conversation_id=conversation_id, cursor=cursor, limit=limit)


@conversations_router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    request: Request,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> MessageResponse:
    request_id = getattr(request.state, "request_id", None)
    msg, _ = await post_message(
        db=db,
        conversation_id=conversation_id,
        content=payload.content,
        client_message_id=payload.client_message_id,
        sender_type=SenderType.staff,
        sender_user_id=current_user.id,
        request_id=request_id,
    )
    return msg
