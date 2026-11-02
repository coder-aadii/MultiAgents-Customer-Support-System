from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.base import ActionResult, RiskLevel
from app.actions.idempotency import IdempotencyEngine
from app.actions.registry import ActionRegistry
from app.models.action_audit_journal import (
    ActionActorType,
    ActionAuditEventType,
)
from app.models.business_action import ActionRiskLevel, ActionStatus, BusinessAction

logger = logging.getLogger("mcss.actions.executor")


class ActionExecutionError(Exception):
    """Raised when an action execution fails or encounters a policy violation."""


class ActionExecutor:
    """Idempotent action execution engine with lock coordination and audit enforcement."""

    @classmethod
    async def execute_action(
        cls,
        db: AsyncSession,
        action_id: uuid.UUID,
        actor_type: ActionActorType,
        actor_id: str,
    ) -> ActionResult:
        """Executes an action idempotently and transactionally.

        Invariants:
        1. If action is already completed, returns cached result without re-executing.
        2. High or Critical risk actions require supervisor review before execution.
        3. Every state transition writes to ActionAuditJournal.
        """
        now = datetime.now(UTC)

        stmt = (
            select(BusinessAction)
            .where(BusinessAction.id == action_id)
            .with_for_update()
        )
        res = await db.execute(stmt)
        action = res.scalar_one_or_none()
        if not action:
            raise ActionExecutionError(f"Action {action_id} not found.")

        # 1. Idempotency Check: Return cached result if already completed
        if action.status == ActionStatus.completed.value and action.result_json:
            logger.info("Returning cached execution result for action %s", action_id)
            return ActionResult(
                action_id=action.id,
                status=ActionStatus.completed,
                success=True,
                confirmation_number=action.result_json.get("confirmation_number"),
                message=action.result_json.get("message", "Action already completed."),
                reconciled_at=action.updated_at,
                details=action.result_json.get("details", {}),
            )

        # 2. Risk Check: High/Critical actions require supervisor review
        if action.risk_level in (RiskLevel.HIGH.value, RiskLevel.CRITICAL.value):
            if not action.reviewed_by_user_id or not action.reviewed_at:
                raise ActionExecutionError(
                    f"Action {action_id} has risk level {action.risk_level} and requires supervisor approval."
                )

        # 3. Transition to executing
        action.status = ActionStatus.executing.value
        action.execution_attempts += 1
        action.updated_at = now
        await IdempotencyEngine.record_audit_entry(
            db=db,
            action_id=action.id,
            event_type=ActionAuditEventType.executing,
            actor_type=actor_type,
            actor_id=actor_id,
            details={"attempt": action.execution_attempts},
        )
        await db.flush()

        # 4. Lookup action definition
        action_def = ActionRegistry.get(action.action_name)
        if not action_def:
            action.status = ActionStatus.failed.value
            await db.flush()
            raise ActionExecutionError(f"No registered implementation for action '{action.action_name}'.")

        try:
            result = await action_def.execute(db=db, action=action)
        except Exception as exc:
            logger.error("Execution error for action %s: %s", action.id, exc, exc_info=True)
            result = ActionResult(
                action_id=action.id,
                status=ActionStatus.failed,
                success=False,
                message=f"Action execution failed: {exc}",
                details={"error": str(exc)},
            )

        # 5. Record result
        if result.success:
            action.status = ActionStatus.completed.value
            action.result_json = result.model_dump(mode="json")
            action.updated_at = datetime.now(UTC)
            await IdempotencyEngine.record_audit_entry(
                db=db,
                action_id=action.id,
                event_type=ActionAuditEventType.completed,
                actor_type=ActionActorType.system,
                actor_id="action_executor",
                details={
                    "confirmation_number": result.confirmation_number,
                    "result": result.model_dump(mode="json"),
                },
            )
        else:
            action.status = ActionStatus.failed.value
            action.result_json = result.model_dump(mode="json")
            action.updated_at = datetime.now(UTC)
            await IdempotencyEngine.record_audit_entry(
                db=db,
                action_id=action.id,
                event_type=ActionAuditEventType.failed,
                actor_type=ActionActorType.system,
                actor_id="action_executor",
                details={"error_message": result.message, "details": result.details},
            )

        await db.flush()
        return result

