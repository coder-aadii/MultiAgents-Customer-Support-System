from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.errors import ForbiddenError, NotFoundError
from app.database import get_session
from app.dependencies import get_current_user, require_admin, require_staff
from app.models.conversation import Conversation
from app.models.conversation_note import ConversationInternalNote
from app.models.sla_policy import SLAPolicy
from app.models.support_team import SupportTeam
from app.models.team_membership import TeamMembership
from app.models.user import User, UserRole
from app.operations.analytics import AnalyticsService
from app.operations.csat import CSATService
from app.operations.notes import InternalNotesService
from app.operations.routing import CapacityRoutingEngine
from app.operations.teams import AgentPresence, TeamService
from app.tenancy.context import TenantContext

operations_router = APIRouter(tags=["operations"])


# Schemas
class TeamCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    description: str | None = None
    routing_strategy: str = Field(default="least_busy", pattern="^(least_busy|round_robin|skill_match)$")


class TeamMemberAddRequest(BaseModel):
    user_id: uuid.UUID
    is_team_lead: bool = False
    max_concurrent_chats: int = Field(default=5, ge=1, le=20)
    skills: list[str] = Field(default_factory=list)


class ConversationAssignRequest(BaseModel):
    team_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None


class AgentPresenceUpdateRequest(BaseModel):
    status: AgentPresence


class NoteCreateRequest(BaseModel):
    note_text: str = Field(..., min_length=1)
    is_whisper: bool = False


class NoteResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    author_user_id: uuid.UUID
    note_text: str
    is_whisper: bool
    created_at: Any


class SLAPolicyCreateRequest(BaseModel):
    name: str = Field(..., max_length=64)
    priority: str = Field(default="medium", pattern="^(low|medium|high|urgent)$")
    first_response_target_minutes: int = Field(..., ge=1)
    resolution_target_minutes: int = Field(..., ge=1)
    business_hours_schedule: dict[str, Any] = Field(default_factory=dict)


class CSATSubmissionRequest(BaseModel):
    token: str
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None


# Endpoints
@operations_router.get("/admin/operations/agents")
async def list_agents(
    limit: int | None = Query(None, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]] | dict[str, Any]:
    org_id = TenantContext.get_tenant_id()

    # Base query
    stmt = (
        select(User)
        .where(User.organization_id == org_id, User.is_active.is_(True))
        .order_by(User.display_name)
    )

    total = None
    if limit is not None:
        count_stmt = select(func.count(User.id)).where(
            User.organization_id == org_id, User.is_active.is_(True)
        )
        total = (await db.execute(count_stmt)).scalar_one()
        stmt = stmt.offset(offset).limit(limit)

    res = await db.execute(stmt)
    users = res.scalars().all()

    if users:
        user_ids = [u.id for u in users]
        mem_stmt = (
            select(TeamMembership, SupportTeam.name)
            .join(SupportTeam, SupportTeam.id == TeamMembership.team_id)
            .where(
                SupportTeam.organization_id == org_id,
                TeamMembership.user_id.in_(user_ids),
            )
        )
        mem_res = await db.execute(mem_stmt)
        user_teams_map: dict[uuid.UUID, list[str]] = {}
        for mem, team_name in mem_res.all():
            user_teams_map.setdefault(mem.user_id, []).append(team_name)
    else:
        user_teams_map = {}

    agents = []
    for u in users:
        presence = TeamService.get_agent_presence(u.id)
        active_count = await TeamService.get_agent_active_chat_count(db, u.id)
        agents.append(
            {
                "id": str(u.id),
                "display_name": u.display_name,
                "email": u.email,
                "role": u.role.value if hasattr(u.role, "value") else str(u.role),
                "presence": presence.value if hasattr(presence, "value") else str(presence),
                "active_chats": active_count,
                "teams": user_teams_map.get(u.id, []),
            }
        )

    if limit is not None:
        return {"items": agents, "total": total, "has_more": offset + len(agents) < (total or 0)}
    return agents


@operations_router.get("/admin/operations/teams")
async def list_teams(
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    org_id = TenantContext.get_tenant_id()
    stmt = select(SupportTeam).options(selectinload(SupportTeam.memberships)).where(SupportTeam.organization_id == org_id)
    res = await db.execute(stmt)
    teams = res.scalars().all()
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "description": t.description,
            "routing_strategy": t.routing_strategy,
            "member_count": len(t.memberships),
        }
        for t in teams
    ]


@operations_router.post("/admin/operations/teams")
async def create_team(
    payload: TeamCreateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    org_id = TenantContext.get_tenant_id()
    team = await TeamService.create_team(
        db=db,
        org_id=org_id,
        name=payload.name,
        description=payload.description,
        routing_strategy=payload.routing_strategy,
    )
    return {"id": str(team.id), "name": team.name, "routing_strategy": team.routing_strategy}


@operations_router.post("/admin/operations/teams/{team_id}/members")
async def add_team_member(
    team_id: uuid.UUID,
    payload: TeamMemberAddRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    mem = await TeamService.add_member(
        db=db,
        team_id=team_id,
        user_id=payload.user_id,
        is_team_lead=payload.is_team_lead,
        max_concurrent_chats=payload.max_concurrent_chats,
        skills=payload.skills,
    )
    return {"id": str(mem.id), "team_id": str(mem.team_id), "user_id": str(mem.user_id)}


@operations_router.get("/admin/operations/agent-status")
async def get_agent_status(
    current_user: User = Depends(require_staff),
) -> dict[str, str]:
    presence = TeamService.get_agent_presence(current_user.id)
    return {"status": presence.value if hasattr(presence, "value") else str(presence), "user_id": str(current_user.id)}


@operations_router.patch("/admin/operations/agent-status")
async def update_agent_status(
    payload: AgentPresenceUpdateRequest,
    current_user: User = Depends(require_staff),
) -> dict[str, str]:
    TeamService.set_agent_presence(current_user.id, payload.status)
    return {"status": payload.status.value, "user_id": str(current_user.id)}


@operations_router.post("/admin/operations/conversations/{conversation_id}/assign")
async def reassign_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationAssignRequest,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    # Require admin or supervisor role to reassign
    if current_user.role != UserRole.admin:
        raise ForbiddenError("Supervisor or admin privilege required to reassign conversations")

    conv_stmt = select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    conv_res = await db.execute(conv_stmt)
    conv = conv_res.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Conversation not found")

    if payload.team_id and not payload.agent_id:
        assigned_user = await CapacityRoutingEngine.route_conversation(db, conversation_id, payload.team_id)
        return {"assigned_team_id": str(payload.team_id), "assigned_user_id": str(assigned_user) if assigned_user else None}

    if payload.agent_id:
        conv.assigned_user_id = payload.agent_id
        conv.assigned_agent_id = payload.agent_id
    if payload.team_id:
        conv.assigned_team_id = payload.team_id

    await db.commit()
    return {"assigned_team_id": str(conv.assigned_team_id) if conv.assigned_team_id else None, "assigned_user_id": str(conv.assigned_user_id) if conv.assigned_user_id else None}


# Internal Notes Endpoints
@operations_router.post("/conversations/{conversation_id}/notes", response_model=NoteResponse)
async def create_note(
    conversation_id: uuid.UUID,
    payload: NoteCreateRequest,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> NoteResponse:
    note = await InternalNotesService.add_note(
        db=db,
        conversation_id=conversation_id,
        author_user=current_user,
        note_text=payload.note_text,
        is_whisper=payload.is_whisper,
    )
    return NoteResponse(
        id=note.id,
        conversation_id=note.conversation_id,
        author_user_id=note.author_user_id,
        note_text=note.note_text,
        is_whisper=note.is_whisper,
        created_at=note.created_at,
    )


@operations_router.get("/conversations/{conversation_id}/notes", response_model=list[NoteResponse])
async def list_notes(
    conversation_id: uuid.UUID,
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> list[NoteResponse]:
    notes = await InternalNotesService.list_notes(
        db=db,
        conversation_id=conversation_id,
        requesting_user=current_user,
    )
    return [
        NoteResponse(
            id=n.id,
            conversation_id=n.conversation_id,
            author_user_id=n.author_user_id,
            note_text=n.note_text,
            is_whisper=n.is_whisper,
            created_at=n.created_at,
        )
        for n in notes
    ]


# SLA Policies
@operations_router.post("/admin/operations/sla-scan")
async def trigger_sla_scan(
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    org_id = TenantContext.get_tenant_id()
    from app.operations.sla import SLAEngine
    return await SLAEngine.run_sla_scan(db, org_id=org_id)


@operations_router.get("/admin/operations/sla-policies")
async def list_sla_policies(
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    org_id = TenantContext.get_tenant_id()
    stmt = select(SLAPolicy).where(SLAPolicy.organization_id == org_id)
    res = await db.execute(stmt)
    policies = res.scalars().all()
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "priority": p.priority,
            "first_response_target_minutes": p.first_response_target_minutes,
            "resolution_target_minutes": p.resolution_target_minutes,
            "is_active": p.is_active,
        }
        for p in policies
    ]


@operations_router.post("/admin/operations/sla-policies")
async def create_sla_policy(
    payload: SLAPolicyCreateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    org_id = TenantContext.get_tenant_id()
    policy = SLAPolicy(
        organization_id=org_id,
        name=payload.name,
        priority=payload.priority,
        first_response_target_minutes=payload.first_response_target_minutes,
        resolution_target_minutes=payload.resolution_target_minutes,
        business_hours_schedule_json=payload.business_hours_schedule,
        is_active=True,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return {"id": str(policy.id), "name": policy.name, "priority": policy.priority}


# CSAT Public Survey Endpoint
@operations_router.post("/csat/submit")
async def submit_csat_survey(
    payload: CSATSubmissionRequest,
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    survey = await CSATService.submit_survey(
        db=db,
        token=payload.token,
        rating=payload.rating,
        comment=payload.comment,
    )
    return {"status": "success", "survey_id": str(survey.id)}


# Operational Analytics
@operations_router.get("/admin/analytics/realtime")
async def get_realtime_analytics(
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    org_id = TenantContext.get_tenant_id()
    return await AnalyticsService.get_realtime_analytics(db, org_id)


@operations_router.get("/admin/analytics/metrics")
async def get_metrics(
    current_user: User = Depends(require_staff),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    org_id = TenantContext.get_tenant_id()
    return await AnalyticsService.get_historical_metrics(db, org_id)

