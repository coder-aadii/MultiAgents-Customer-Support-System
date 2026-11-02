from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.sla_policy import SLAPolicy


class SLAEngine:
    """Calculates SLA elapsed time, enforces business hours, and detects threshold warnings & breaches."""

    @classmethod
    def calculate_business_minutes(
        cls,
        start_dt: datetime,
        end_dt: datetime,
        schedule: dict[str, Any] | None = None,
    ) -> float:
        """Computes business minutes elapsed between start_dt and end_dt."""
        if end_dt <= start_dt:
            return 0.0

        sched = schedule or {}
        # Default: 09:00 - 17:00 Monday to Friday (days 0 to 4)
        start_hour = int(sched.get("start_hour", 9))
        end_hour = int(sched.get("end_hour", 17))

        total_minutes = 0.0
        curr = start_dt
        while curr < end_dt:
            # Check weekday
            if curr.weekday() < 5 and start_hour <= curr.hour < end_hour:
                total_minutes += 1.0
            curr += timedelta(minutes=1)

        return total_minutes

    @classmethod
    async def evaluate_sla(
        cls,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        now: datetime | None = None,
    ) -> tuple[str, float, float]:
        """Evaluates conversation against its assigned SLA policy.

        Returns:
            (status: "normal" | "warning" | "breached", elapsed_minutes, target_minutes)
        """
        curr_time = now or datetime.now(UTC)
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id).with_for_update()
        conv_res = await db.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()
        if not conv or not conv.sla_policy_id:
            return "normal", 0.0, 0.0

        policy_stmt = select(SLAPolicy).where(SLAPolicy.id == conv.sla_policy_id)
        policy_res = await db.execute(policy_stmt)
        policy = policy_res.scalar_one_or_none()
        if not policy:
            return "normal", 0.0, 0.0

        target_minutes = float(policy.first_response_target_minutes)
        end_time = conv.first_response_at or curr_time
        elapsed = cls.calculate_business_minutes(conv.created_at, end_time, policy.business_hours_schedule_json)

        status = "normal"
        if elapsed >= target_minutes:
            status = "breached"
            conv.sla_breached = True
            await db.commit()
        elif elapsed >= 0.8 * target_minutes:
            status = "warning"

        return status, elapsed, target_minutes

    @classmethod
    async def mark_first_response(
        cls,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        response_time: datetime | None = None,
    ) -> None:
        stmt = select(Conversation).where(Conversation.id == conversation_id)
        res = await db.execute(stmt)
        conv = res.scalar_one_or_none()
        if conv and not conv.first_response_at:
            conv.first_response_at = response_time or datetime.now(UTC)
            await db.commit()

    @classmethod
    async def mark_resolution(
        cls,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        resolved_time: datetime | None = None,
    ) -> None:
        stmt = select(Conversation).where(Conversation.id == conversation_id)
        res = await db.execute(stmt)
        conv = res.scalar_one_or_none()
        if conv and not conv.resolved_at:
            conv.resolved_at = resolved_time or datetime.now(UTC)
            await db.commit()

    @classmethod
    async def run_sla_scan(
        cls,
        db: AsyncSession,
        org_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Scans active conversations with SLA policies, evaluates warnings, and marks breaches."""
        from app.models.conversation import ConversationStatus

        stmt = (
            select(Conversation)
            .where(
                Conversation.sla_policy_id.is_not(None),
                Conversation.status.in_([ConversationStatus.open, ConversationStatus.waiting_for_staff, ConversationStatus.staff_active]),
            )
            .with_for_update()
        )
        if org_id:
            stmt = stmt.where(Conversation.organization_id == org_id)

        res = await db.execute(stmt)
        conversations = res.scalars().all()

        results = []
        for conv in conversations:
            status, elapsed, target = await cls.evaluate_sla(db, conv.id, now=now)
            results.append({
                "conversation_id": str(conv.id),
                "status": status,
                "elapsed_minutes": elapsed,
                "target_minutes": target,
                "sla_breached": conv.sla_breached,
            })

        await db.commit()
        return results

