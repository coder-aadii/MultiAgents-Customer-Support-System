from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationStatus
from app.models.csat_survey import CSATSurvey
from app.models.support_team import SupportTeam


class AnalyticsService:
    """Aggregates operational metrics, SLA health, queue depths, and customer satisfaction distributions."""

    @classmethod
    async def get_realtime_analytics(
        cls,
        db: AsyncSession,
        org_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Real-time operational snapshot."""
        # 1. Conversation state distribution
        status_stmt = (
            select(Conversation.status, func.count(Conversation.id))
            .where(Conversation.organization_id == org_id)
            .group_by(Conversation.status)
        )
        status_res = await db.execute(status_stmt)
        status_counts = {row[0].value if hasattr(row[0], "value") else str(row[0]): row[1] for row in status_res.all()}

        # 2. SLA breaches
        breached_stmt = (
            select(func.count(Conversation.id))
            .where(Conversation.organization_id == org_id, Conversation.sla_breached.is_(True))
        )
        breached_res = await db.execute(breached_stmt)
        breached_count = int(breached_res.scalar() or 0)

        # 3. Distinct assigned agents handling open tickets
        agents_stmt = (
            select(func.count(func.distinct(Conversation.assigned_user_id)))
            .where(
                Conversation.organization_id == org_id,
                Conversation.assigned_user_id.isnot(None),
                Conversation.status.in_([ConversationStatus.staff_active, ConversationStatus.open]),
            )
        )
        agents_res = await db.execute(agents_stmt)
        assigned_agents_count = int(agents_res.scalar() or 0)

        # 4. Queue depths by team
        team_stmt = (
            select(SupportTeam.id, SupportTeam.name, func.count(Conversation.id))
            .outerjoin(Conversation, Conversation.assigned_team_id == SupportTeam.id)
            .where(SupportTeam.organization_id == org_id)
            .group_by(SupportTeam.id, SupportTeam.name)
        )
        team_res = await db.execute(team_stmt)
        queues = [{"team_id": str(r[0]), "team_name": r[1], "depth": r[2]} for r in team_res.all()]

        total_open = status_counts.get("staff_active", 0) + status_counts.get("open", 0)
        return {
            "total_open_tickets": total_open,
            "active_chats": total_open,
            "waiting_for_staff": status_counts.get("waiting_for_staff", 0),
            "closed_chats": status_counts.get("closed", 0),
            "active_sla_breaches": breached_count,
            "sla_breaches": breached_count,
            "assigned_agents": assigned_agents_count,
            "queues": queues,
        }

    @classmethod
    async def get_historical_metrics(
        cls,
        db: AsyncSession,
        org_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Historical KPI analysis: Average FRT, Resolution Time, and CSAT scores."""
        # 1. CSAT averages & distribution
        csat_stmt = (
            select(func.avg(CSATSurvey.rating), func.count(CSATSurvey.id))
            .where(CSATSurvey.organization_id == org_id)
        )
        csat_res = await db.execute(csat_stmt)
        avg_rating, total_surveys = csat_res.one()

        dist_stmt = (
            select(CSATSurvey.rating, func.count(CSATSurvey.id))
            .where(CSATSurvey.organization_id == org_id)
            .group_by(CSATSurvey.rating)
        )
        dist_res = await db.execute(dist_stmt)
        dist_dict = {i: 0 for i in range(1, 6)}
        for rating, count in dist_res.all():
            if rating in dist_dict:
                dist_dict[rating] = count

        # 2. FRT & Resolution Time approximations
        conv_times_stmt = (
            select(
                func.avg(
                    func.extract("epoch", Conversation.first_response_at) - func.extract("epoch", Conversation.created_at)
                ),
                func.avg(
                    func.extract("epoch", Conversation.resolved_at) - func.extract("epoch", Conversation.created_at)
                ),
            )
            .where(Conversation.organization_id == org_id)
        )
        times_res = await db.execute(times_times := conv_times_stmt)
        avg_frt_sec, avg_rt_sec = times_res.one()

        return {
            "average_first_response_minutes": round(float(avg_frt_sec or 0) / 60.0, 1),
            "average_resolution_minutes": round(float(avg_rt_sec or 0) / 60.0, 1),
            "average_csat_score": round(float(avg_rating or 0.0), 2),
            "total_csat_surveys": int(total_surveys or 0),
            "csat_distribution": dist_dict,
        }

