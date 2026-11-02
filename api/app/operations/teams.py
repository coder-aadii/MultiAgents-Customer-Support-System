from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationStatus
from app.models.support_team import SupportTeam
from app.models.team_membership import TeamMembership
from app.models.user import User


class AgentPresence(str, enum.Enum):
    ONLINE = "online"
    BUSY = "busy"
    AWAY = "away"
    OFFLINE = "offline"


class TeamService:
    """Manages support teams, agent memberships, and live availability presence."""

    # In-memory presence map: user_id -> AgentPresence
    _agent_presence: dict[uuid.UUID, AgentPresence] = {}

    @classmethod
    def set_agent_presence(cls, user_id: uuid.UUID, presence: AgentPresence) -> None:
        cls._agent_presence[user_id] = presence

    @classmethod
    def get_agent_presence(cls, user_id: uuid.UUID) -> AgentPresence:
        return cls._agent_presence.get(user_id, AgentPresence.ONLINE)

    @classmethod
    def reset_presence(cls) -> None:
        cls._agent_presence.clear()

    @classmethod
    async def create_team(
        cls,
        db: AsyncSession,
        org_id: uuid.UUID,
        name: str,
        description: str | None = None,
        routing_strategy: str = "least_busy",
    ) -> SupportTeam:
        team = SupportTeam(
            organization_id=org_id,
            name=name.strip(),
            description=description,
            routing_strategy=routing_strategy,
        )
        db.add(team)
        await db.commit()
        await db.refresh(team)
        return team

    @classmethod
    async def add_member(
        cls,
        db: AsyncSession,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
        is_team_lead: bool = False,
        max_concurrent_chats: int = 5,
        skills: list[str] | None = None,
    ) -> TeamMembership:
        membership = TeamMembership(
            team_id=team_id,
            user_id=user_id,
            is_team_lead=is_team_lead,
            max_concurrent_chats=max_concurrent_chats,
            skills=skills or [],
        )
        db.add(membership)
        await db.commit()
        await db.refresh(membership)
        return membership

    @classmethod
    async def get_agent_active_chat_count(cls, db: AsyncSession, user_id: uuid.UUID) -> int:
        stmt = (
            select(func.count(Conversation.id))
            .where(
                Conversation.assigned_user_id == user_id,
                Conversation.status.in_([ConversationStatus.staff_active, ConversationStatus.open]),
            )
        )
        res = await db.execute(stmt)
        return int(res.scalar() or 0)

