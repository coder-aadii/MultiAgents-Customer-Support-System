from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.cursor import decode_cursor, encode_cursor
from app.database import get_session
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.schemas.audit import AuditEventResponse
from app.schemas.common import PaginatedResponse
from app.tenancy.filter import TenantQueryScoper
from app.tenancy.rbac import require_org_role
from app.tenancy.resolver import resolve_tenant

audit_router = APIRouter(prefix="/audit", tags=["audit"])


@audit_router.get("/events", response_model=PaginatedResponse[AuditEventResponse])
async def list_audit_events(
    event_type: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    org: Organization = Depends(resolve_tenant),
    _membership: OrganizationMembership = Depends(
        require_org_role(
            OrgMemberRole.agent,
            OrgMemberRole.supervisor,
            OrgMemberRole.admin,
            OrgMemberRole.owner,
        )
    ),
    db: AsyncSession = Depends(get_session),
) -> PaginatedResponse[AuditEventResponse]:
    """Retrieve an immutable, paginated security audit journal scoped to active organization."""
    query = select(AuditEvent).where(AuditEvent.organization_id == org.id)
    query = TenantQueryScoper.apply_scope(query, AuditEvent)

    if event_type:
        query = query.where(AuditEvent.event_type == event_type.strip())
    if resource_type:
        query = query.where(AuditEvent.resource_type == resource_type.strip())

    if cursor:
        dt, item_id = decode_cursor(cursor)
        query = query.where(
            or_(
                AuditEvent.created_at < dt,
                and_(AuditEvent.created_at == dt, AuditEvent.id < item_id),
            )
        )

    query = query.order_by(desc(AuditEvent.created_at), desc(AuditEvent.id)).limit(limit + 1)
    result = await db.execute(query)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(last.created_at, last.id)

    return PaginatedResponse(
        items=[AuditEventResponse.model_validate(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )

