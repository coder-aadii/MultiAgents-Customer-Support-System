from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.base import ActionDraft, ActionResult, RiskLevel
from app.models.action_audit_journal import (
    ActionActorType,
    ActionAuditEventType,
    ActionAuditJournal,
)
from app.models.business_action import ActionRiskLevel, ActionStatus, BusinessAction

logger = logging.getLogger("mcss.actions.idempotency")

DEFAULT_EXPIRATION_MINUTES = 10


def generate_idempotency_key(
    conversation_id: uuid.UUID,
    customer_id: str,
    action_name: str,
    payload: dict[str, Any],
) -> str:
    """Generates a deterministic SHA256 digest representing the unique business operation intent."""
    normalized_payload = json.dumps(payload, sort_keys=True)
    raw_material = f"{conversation_id}:{customer_id}:{action_name}:{normalized_payload}"
    return hashlib.sha256(raw_material.encode("utf-8")).hexdigest()[:48]


class IdempotencyEngine:
    """Manages atomic action draft generation, state locking, deduplication, and immutable audit logs."""

    @staticmethod
    async def record_audit_entry(
        db: AsyncSession,
        action_id: uuid.UUID,
        event_type: ActionAuditEventType,
        actor_type: ActionActorType,
        actor_id: str,
        details: dict[str, Any] | None = None,
    ) -> ActionAuditJournal:
        """Appends an immutable audit entry for an action lifecycle transition."""
        entry = ActionAuditJournal(
            action_id=action_id,
            event_type=event_type.value,
            actor_type=actor_type.value,
            actor_id=actor_id,
            details_json=details or {},
            created_at=datetime.now(UTC),
        )
        db.add(entry)
        await db.flush()
        return entry

    @classmethod
    async def create_or_get_draft(
        cls,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        customer_id: str,
        action_name: str,
        risk_level: RiskLevel,
        summary_message: str,
        required_confirmation_phrase: str,
        payload: dict[str, Any],
        expires_in_minutes: int = DEFAULT_EXPIRATION_MINUTES,
    ) -> tuple[ActionDraft, bool]:
        """Creates an action draft atomically or returns an existing valid one.

        Returns:
            (ActionDraft, is_newly_created)
        """
        idempotency_key = generate_idempotency_key(
            conversation_id=conversation_id,
            customer_id=customer_id,
            action_name=action_name,
            payload=payload,
        )

        now = datetime.now(UTC)

        # Acquire transaction-scoped advisory lock on the idempotency key to prevent race conditions on concurrent inserts
        await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(idempotency_key))))

        # Check for existing action using row lock
        stmt = (
            select(BusinessAction)
            .where(BusinessAction.idempotency_key == idempotency_key)
            .with_for_update()
        )
        res = await db.execute(stmt)
        existing = res.scalar_one_or_none()

        if existing:
            # If expired and awaiting confirmation, mark expired
            if existing.status == ActionStatus.awaiting_confirmation.value and existing.expires_at <= now:
                existing.status = ActionStatus.expired.value
                await cls.record_audit_entry(
                    db=db,
                    action_id=existing.id,
                    event_type=ActionAuditEventType.expired,
                    actor_type=ActionActorType.system,
                    actor_id="system_expiry",
                    details={"reason": "10m draft timeout exceeded"},
                )
                await db.flush()
            else:
                # Return existing draft
                draft = ActionDraft(
                    action_id=existing.id,
                    action_name=existing.action_name,
                    risk_level=RiskLevel(existing.risk_level),
                    summary_message=existing.summary_message,
                    required_confirmation_phrase=existing.required_confirmation_phrase,
                    idempotency_key=existing.idempotency_key,
                    expires_at=existing.expires_at,
                    parameters=existing.payload_json,
                )
                return draft, False

        # Create new BusinessAction
        action_id = uuid.uuid4()
        expires_at = now + timedelta(minutes=expires_in_minutes)

        new_action = BusinessAction(
            id=action_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            action_name=action_name,
            risk_level=risk_level.value,
            status=ActionStatus.awaiting_confirmation.value,
            idempotency_key=idempotency_key,
            payload_json=payload,
            summary_message=summary_message,
            required_confirmation_phrase=required_confirmation_phrase,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        db.add(new_action)
        await db.flush()

        await cls.record_audit_entry(
            db=db,
            action_id=action_id,
            event_type=ActionAuditEventType.proposed,
            actor_type=ActionActorType.system,
            actor_id="assistant_policy_gate",
            details={
                "action_name": action_name,
                "risk_level": risk_level.value,
                "expires_at": expires_at.isoformat(),
                "payload": payload,
            },
        )

        draft = ActionDraft(
            action_id=action_id,
            action_name=action_name,
            risk_level=risk_level,
            summary_message=summary_message,
            required_confirmation_phrase=required_confirmation_phrase,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
            parameters=payload,
        )
        return draft, True

    @classmethod
    async def get_active_pending_action(
        cls,
        db: AsyncSession,
        conversation_id: uuid.UUID,
    ) -> BusinessAction | None:
        """Finds any active, unexpired action awaiting confirmation in this conversation."""
        now = datetime.now(UTC)
        stmt = (
            select(BusinessAction)
            .where(
                BusinessAction.conversation_id == conversation_id,
                BusinessAction.status == ActionStatus.awaiting_confirmation.value,
            )
            .order_by(desc(BusinessAction.created_at))
            .limit(1)
            .with_for_update()
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none()

    @classmethod
    async def cancel_action(
        cls,
        db: AsyncSession,
        action_id: uuid.UUID,
        actor_id: str,
        reason: str = "customer_cancelled",
    ) -> BusinessAction | None:
        """Cancels a pending action draft."""
        stmt = (
            select(BusinessAction)
            .where(BusinessAction.id == action_id)
            .with_for_update()
        )
        res = await db.execute(stmt)
        action = res.scalar_one_or_none()
        if not action:
            return None

        if action.status not in (ActionStatus.drafted.value, ActionStatus.awaiting_confirmation.value):
            return action

        action.status = ActionStatus.cancelled.value
        action.updated_at = datetime.now(UTC)
        await cls.record_audit_entry(
            db=db,
            action_id=action.id,
            event_type=ActionAuditEventType.cancelled,
            actor_type=ActionActorType.customer,
            actor_id=actor_id,
            details={"reason": reason},
        )
        await db.flush()
        return action
