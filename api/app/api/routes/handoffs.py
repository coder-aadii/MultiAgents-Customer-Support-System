from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.dependencies import require_staff
from app.models.handoff import HandoffStatus
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.handoff import HandoffResponse
from app.services.conversation_service import claim_handoff, list_handoffs

handoffs_router = APIRouter(prefix="/handoffs", tags=["handoffs"])


@handoffs_router.get("", response_model=PaginatedResponse[HandoffResponse])
async def get_handoffs(
    status: HandoffStatus = Query(default=HandoffStatus.queued),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> PaginatedResponse[HandoffResponse]:
    return await list_handoffs(db, status=status, cursor=cursor, limit=limit)


@handoffs_router.post("/{handoff_id}/claim", response_model=HandoffResponse)
async def claim(
    handoff_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> HandoffResponse:
    request_id = getattr(request.state, "request_id", None)
    return await claim_handoff(
        db, handoff_id=handoff_id, staff_user=current_user, request_id=request_id
    )
