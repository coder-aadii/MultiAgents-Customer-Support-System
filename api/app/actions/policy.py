from __future__ import annotations

import enum
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.base import ActionDraft, RiskLevel
from app.actions.idempotency import IdempotencyEngine
from app.models.action_audit_journal import (
    ActionActorType,
    ActionAuditEventType,
)
from app.models.business_action import ActionRiskLevel, ActionStatus, BusinessAction

logger = logging.getLogger("mcss.actions.policy")


class ConfirmationDecision(str, enum.Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    AMBIGUOUS = "ambiguous"
    NO_ACTIVE_ACTION = "no_active_action"


class ConfirmationEvaluation(BaseModel):
    decision: ConfirmationDecision
    action: Any | None = None
    response_message: str | None = None


def normalize_phrase(text: str) -> str:
    """Normalizes phrase by stripping punctuation and consolidating whitespace."""
    cleaned = re.sub(r"[^\w\s-]", "", text.strip())
    return re.sub(r"\s+", " ", cleaned).strip().upper()


class ActionPolicyGate:
    """Deterministic policy gate enforcing strict two-phase confirmation and risk boundaries."""

    CANCELLATION_PHRASES = {
        "CANCEL",
        "ABORT",
        "STOP",
        "NO",
        "DONT DO IT",
        "NEVER MIND",
        "NEVERMIND",
    }

    @classmethod
    async def evaluate_customer_reply(
        cls,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        customer_id: str,
        message_content: str,
    ) -> ConfirmationEvaluation:
        """Evaluates whether an incoming customer message confirms or cancels a pending action."""
        action = await IdempotencyEngine.get_active_pending_action(
            db=db, conversation_id=conversation_id
        )
        if not action:
            return ConfirmationEvaluation(decision=ConfirmationDecision.NO_ACTIVE_ACTION)

        # Ensure customer identity matches
        if action.customer_id != customer_id:
            logger.warning(
                "Cross-customer action access attempt on action %s by customer %s",
                action.id,
                customer_id,
            )
            return ConfirmationEvaluation(decision=ConfirmationDecision.NO_ACTIVE_ACTION)

        now = datetime.now(UTC)

        # 1. Check expiration
        if action.expires_at <= now:
            action.status = ActionStatus.expired.value
            action.updated_at = now
            await IdempotencyEngine.record_audit_entry(
                db=db,
                action_id=action.id,
                event_type=ActionAuditEventType.expired,
                actor_type=ActionActorType.system,
                actor_id="system_expiry",
                details={"reason": "10m draft timeout exceeded"},
            )
            await db.flush()
            return ConfirmationEvaluation(
                decision=ConfirmationDecision.EXPIRED,
                action=action,
                response_message=(
                    f"Your request to {action.summary_message.lower()} has expired. "
                    "Please let me know if you would still like assistance with this."
                ),
            )

        norm_msg = normalize_phrase(message_content)
        norm_expected = normalize_phrase(action.required_confirmation_phrase)

        # 2. Check cancellation
        if norm_msg in cls.CANCELLATION_PHRASES:
            cancelled = await IdempotencyEngine.cancel_action(
                db=db,
                action_id=action.id,
                actor_id=customer_id,
                reason="Customer explicit cancellation",
            )
            return ConfirmationEvaluation(
                decision=ConfirmationDecision.CANCELLED,
                action=cancelled,
                response_message=f"I have cancelled your request to {action.summary_message.lower()}.",
            )

        # 3. Check exact confirmation
        if norm_msg == norm_expected:
            action.confirmed_at = now
            action.updated_at = now

            # If HIGH or CRITICAL risk, requires staff approval
            if action.risk_level in (RiskLevel.HIGH.value, RiskLevel.CRITICAL.value):
                action.status = ActionStatus.awaiting_approval.value
                await IdempotencyEngine.record_audit_entry(
                    db=db,
                    action_id=action.id,
                    event_type=ActionAuditEventType.confirmed,
                    actor_type=ActionActorType.customer,
                    actor_id=customer_id,
                    details={"risk_level": action.risk_level, "escalated_to_supervisor": True},
                )
                await db.flush()
                return ConfirmationEvaluation(
                    decision=ConfirmationDecision.CONFIRMED,
                    action=action,
                    response_message=(
                        f"Thank you. Your request to {action.summary_message.lower()} has been submitted "
                        "for supervisor review. An agent will review and process it shortly."
                    ),
                )
            else:
                action.status = ActionStatus.executing.value
                await IdempotencyEngine.record_audit_entry(
                    db=db,
                    action_id=action.id,
                    event_type=ActionAuditEventType.confirmed,
                    actor_type=ActionActorType.customer,
                    actor_id=customer_id,
                    details={"risk_level": action.risk_level, "ready_for_execution": True},
                )
                await db.flush()
                return ConfirmationEvaluation(
                    decision=ConfirmationDecision.CONFIRMED,
                    action=action,
                )

        # 4. Ambiguous / Partial Match
        return ConfirmationEvaluation(
            decision=ConfirmationDecision.AMBIGUOUS,
            action=action,
            response_message=(
                f"You have a pending action: {action.summary_message}. "
                f"To confirm and proceed, please reply with the exact phrase: "
                f"\"{action.required_confirmation_phrase}\", or reply CANCEL to abort."
            ),
        )

