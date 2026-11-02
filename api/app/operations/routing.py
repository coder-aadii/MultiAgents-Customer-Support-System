from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.conversation import Conversation, ConversationStatus
from app.models.support_team import SupportTeam
from app.models.team_membership import TeamMembership
from app.operations.teams import AgentPresence, TeamService


class CapacityRoutingEngine:
    """Intelligent queue routing engine matching conversations to agents based on capacity, presence, and skills."""

    _round_robin_counter: dict[uuid.UUID, int] = {}

    @classmethod
    async def route_conversation(
        cls,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        team_id: uuid.UUID,
        required_skills: list[str] | None = None,
    ) -> uuid.UUID | None:
        """Assigns conversation to best eligible agent within target team. Returns assigned user_id or None."""
        team_stmt = select(SupportTeam).options(selectinload(SupportTeam.memberships)).where(SupportTeam.id == team_id)
        team_res = await db.execute(team_stmt)
        team = team_res.scalar_one_or_none()
        if not team or not team.memberships:
            return None

        conv_stmt = select(Conversation).where(Conversation.id == conversation_id).with_for_update()
        conv_res = await db.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()
        if not conv:
            return None

        # Filter eligible members
        eligible_members: list[tuple[TeamMembership, int]] = []
        for member in team.memberships:
            # 1. Presence check
            presence = TeamService.get_agent_presence(member.user_id)
            if presence not in (AgentPresence.ONLINE,):
                continue

            # 2. Capacity check
            active_chats = await TeamService.get_agent_active_chat_count(db, member.user_id)
            if active_chats >= member.max_concurrent_chats:
                continue

            # 3. Skill match check
            if required_skills:
                member_skills = set(member.skills or [])
                if not set(required_skills).issubset(member_skills):
                    continue

            eligible_members.append((member, active_chats))

        if not eligible_members:
            # Assign team only, keep waiting for available agent
            conv.assigned_team_id = team_id
            await db.commit()
            return None

        # Strategy Selection
        strategy = team.routing_strategy or "least_busy"
        selected_member: TeamMembership | None = None

        if strategy == "least_busy" or strategy == "skill_match":
            # Sort by least active chats
            eligible_members.sort(key=lambda x: x[1])
            selected_member = eligible_members[0][0]
        else:
            # Round-robin
            idx = cls._round_robin_counter.get(team_id, 0) % len(eligible_members)
            cls._round_robin_counter[team_id] = idx + 1
            selected_member = eligible_members[idx][0]

        # Apply assignment
        assigned_user_id = selected_member.user_id
        conv.assigned_team_id = team_id
        conv.assigned_user_id = assigned_user_id
        conv.assigned_agent_id = assigned_user_id
        conv.status = ConversationStatus.staff_active
        await db.commit()
        await db.refresh(conv)

        return assigned_user_id

