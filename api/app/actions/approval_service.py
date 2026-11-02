from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.base import ActionResult
from app.actions.executor import ActionExecutor
from app.actions.idempotency import IdempotencyEngine
from app.models.action_audit_journal import (
    ActionActorType,
    ActionAuditEventType,
)
from app.models.business_action import ActionRiskLevel, ActionStatus, BusinessAction

logger = logging.getLogger("mcss.actions.approval")


class ApprovalError(Exception):
    """Raised when an approval workflow encounters an invalid state or permission violation."""


class ApprovalService:
    """Manages staff supervisor review queues and dual-authorization for high-risk actions."""

    @classmethod
    async def list_pending_actions(
        cls,
        db: AsyncSession,
        risk_level: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BusinessAction]:
        """Lists actions awaiting staff supervisor approval."""
        stmt = (
            select(BusinessAction)
            .where(BusinessAction.status == ActionStatus.awaiting_approval.value)
            .order_by(desc(BusinessAction.created_at))
            .limit(limit)
            .offset(offset)
        )
        if risk_level:
            stmt = stmt.where(BusinessAction.risk_level == risk_level)

        res = await db.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def approve_action(
        cls,
        db: AsyncSession,
        action_id: uuid.UUID,
        supervisor_user_id: uuid.UUID,
        notes: str | None = None,
    ) -> ActionResult:
        """Approves a high-risk action and triggers its execution immediately."""
        stmt = (
            select(BusinessAction)
            .where(BusinessAction.id == action_id)
            .with_for_update()
        )
        res = await db.execute(stmt)
        action = res.scalar_one_or_none()
        if not action:
            raise ApprovalError(f"Action {action_id} not found.")

        if action.status != ActionStatus.awaiting_approval.value:
            raise ApprovalError(
                f"Action {action_id} cannot be approved from status '{action.status}'."
            )

        now = datetime.now(UTC)
        action.reviewed_by_user_id = supervisor_user_id
        action.reviewed_at = now
        action.review_notes = notes

        await IdempotencyEngine.record_audit_entry(
            db=db,
            action_id=action.id,
            event_type=ActionAuditEventType.approved,
            actor_type=ActionActorType.staff,
            actor_id=str(supervisor_user_id),
            details={"notes": notes},
        )
        await db.flush()

        # Dispatch execution
        result = await ActionExecutor.execute_action(
            db=db,
            action_id=action.id,
            actor_type=ActionActorType.staff,
            actor_id=str(supervisor_user_id),
        )
        return result

    @classmethod
    async def reject_action(
        cls,
        db: AsyncSession,
        action_id: uuid.UUID,
        supervisor_user_id: uuid.UUID,
        reason: str,
    ) -> BusinessAction:
        """Rejects a high-risk action and marks it aborted."""
        stmt = (
            select(BusinessAction)
            .where(BusinessAction.id == action_id)
            .with_for_update()
        )
        res = await db.execute(stmt)
        action = res.scalar_one_or_none()
        if not action:
            raise ApprovalError(f"Action {action_id} not found.")

        if action.status != ActionStatus.awaiting_approval.value:
            raise ApprovalError(
                f"Action {action_id} cannot be rejected from status '{action.status}'."
            )

        now = datetime.now(UTC)
        action.status = ActionStatus.rejected.value
        action.reviewed_by_user_id = supervisor_user_id
        action.reviewed_at = now
        action.review_notes = reason
        action.updated_at = now

        await IdempotencyEngine.record_audit_entry(
            db=db,
            action_id=action.id,
            event_type=ActionAuditEventType.rejected,
            actor_type=ActionActorType.staff,
            actor_id=str(supervisor_user_id),
            details={"reason": reason},
        )
        await db.flush()
        return action

