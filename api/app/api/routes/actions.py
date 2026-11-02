from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.approval_service import ApprovalError, ApprovalService
from app.actions.base import ActionResult
from app.database import get_session
from app.dependencies import require_staff
from app.models.user import User
from app.schemas.actions import (
    ActionApprovalRequest,
    ActionRejectionRequest,
    BusinessActionResponse,
)

actions_router = APIRouter(prefix="/admin/actions", tags=["admin-actions"])


@actions_router.get("/pending", response_model=list[BusinessActionResponse])
async def list_pending_actions(
    risk_level: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> list[BusinessActionResponse]:
    """Lists pending actions awaiting supervisor review."""
    actions = await ApprovalService.list_pending_actions(
        db=db,
        risk_level=risk_level,
        limit=limit,
        offset=offset,
    )
    return [
        BusinessActionResponse(
            id=a.id,
            conversation_id=a.conversation_id,
            customer_id=a.customer_id,
            action_name=a.action_name,
            risk_level=a.risk_level,
            status=a.status,
            idempotency_key=a.idempotency_key,
            payload_json=a.payload_json,
            result_json=a.result_json,
            summary_message=a.summary_message,
            required_confirmation_phrase=a.required_confirmation_phrase,
            expires_at=a.expires_at,
            confirmed_at=a.confirmed_at,
            reviewed_by_user_id=a.reviewed_by_user_id,
            reviewed_at=a.reviewed_at,
            review_notes=a.review_notes,
            execution_attempts=a.execution_attempts,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )
        for a in actions
    ]


@actions_router.post("/{action_id}/approve", response_model=ActionResult)
async def approve_action(
    action_id: uuid.UUID,
    payload: ActionApprovalRequest,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> ActionResult:
    """Supervisor approves a pending high-risk action."""
    try:
        result = await ApprovalService.approve_action(
            db=db,
            action_id=action_id,
            supervisor_user_id=current_user.id,
            notes=payload.notes,
        )
        await db.commit()
        return result
    except ApprovalError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@actions_router.post("/{action_id}/reject", response_model=BusinessActionResponse)
async def reject_action(
    action_id: uuid.UUID,
    payload: ActionRejectionRequest,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> BusinessActionResponse:
    """Supervisor rejects a pending action."""
    try:
        action = await ApprovalService.reject_action(
            db=db,
            action_id=action_id,
            supervisor_user_id=current_user.id,
            reason=payload.reason,
        )
        await db.commit()
        return BusinessActionResponse(
            id=action.id,
            conversation_id=action.conversation_id,
            customer_id=action.customer_id,
            action_name=action.action_name,
            risk_level=action.risk_level,
            status=action.status,
            idempotency_key=action.idempotency_key,
            payload_json=action.payload_json,
            result_json=action.result_json,
            summary_message=action.summary_message,
            required_confirmation_phrase=action.required_confirmation_phrase,
            expires_at=action.expires_at,
            confirmed_at=action.confirmed_at,
            reviewed_by_user_id=action.reviewed_by_user_id,
            reviewed_at=action.reviewed_at,
            review_notes=action.review_notes,
            execution_attempts=action.execution_attempts,
            created_at=action.created_at,
            updated_at=action.updated_at,
        )
    except ApprovalError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

