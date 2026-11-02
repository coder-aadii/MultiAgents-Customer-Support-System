from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Callable, Coroutine

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.idempotency import IdempotencyEngine
from app.models.action_audit_journal import (
    ActionActorType,
    ActionAuditEventType,
)
from app.models.business_action import ActionStatus, BusinessAction

logger = logging.getLogger("mcss.actions.reconciliation")


class ReconciliationService:
    """Verifies that upstream external systems of record match internal action results."""

    @classmethod
    async def reconcile_action(
        cls,
        db: AsyncSession,
        action_id: uuid.UUID,
        upstream_status_verifier: Callable[[BusinessAction], Coroutine[Any, Any, tuple[bool, dict[str, Any]]]],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Reconciles an executed action with its upstream system of record.

        Args:
            db: Active database session
            action_id: Target business action UUID
            upstream_status_verifier: Async callable receiving BusinessAction and returning (is_consistent, details)

        Returns:
            (is_synchronized, status_message, audit_details)
        """
        stmt = select(BusinessAction).where(BusinessAction.id == action_id)
        res = await db.execute(stmt)
        action = res.scalar_one_or_none()
        if not action:
            return False, f"Action {action_id} not found.", {}

        if action.status != ActionStatus.completed.value:
            return False, f"Cannot reconcile uncompleted action (status={action.status}).", {}

        is_consistent, upstream_details = await upstream_status_verifier(action)
        now = datetime.now(UTC)

        if is_consistent:
            await IdempotencyEngine.record_audit_entry(
                db=db,
                action_id=action.id,
                event_type=ActionAuditEventType.reconciled,
                actor_type=ActionActorType.system,
                actor_id="reconciliation_worker",
                details={
                    "status": "synchronized",
                    "upstream_details": upstream_details,
                    "reconciled_at": now.isoformat(),
                },
            )
            await db.flush()
            return True, "State verified and synchronized with external system.", upstream_details
        else:
            logger.error(
                "CRITICAL: State mismatch detected during reconciliation for action %s! Upstream: %s",
                action_id,
                upstream_details,
            )
            await IdempotencyEngine.record_audit_entry(
                db=db,
                action_id=action.id,
                event_type=ActionAuditEventType.reconciled,
                actor_type=ActionActorType.system,
                actor_id="reconciliation_worker",
                details={
                    "status": "mismatch_detected",
                    "alert": "DESYNC_ALERT",
                    "upstream_details": upstream_details,
                    "reconciled_at": now.isoformat(),
                },
            )
            await db.flush()
            return False, "State mismatch detected between action record and external system.", upstream_details

