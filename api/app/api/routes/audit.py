from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.cursor import decode_cursor, encode_cursor
from app.database import get_session
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.schemas.audit import AuditEventResponse
from app.schemas.common import PaginatedResponse
from app.tenancy.filter import TenantQueryScoper
from app.tenancy.rbac import PERM_AUDIT_EXPORT, PERM_AUDIT_READ, require_org_role, require_permission
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
            OrgMemberRole.auditor,
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
        query = query.where(AuditEvent.event_type.ilike(f"%{event_type.strip()}%"))
    if resource_type:
        query = query.where(AuditEvent.resource_type.ilike(f"%{resource_type.strip()}%"))

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


@audit_router.get("/export")
async def export_audit_events(
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    event_type: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
    org: Organization = Depends(resolve_tenant),
    _membership: OrganizationMembership = Depends(require_permission(PERM_AUDIT_EXPORT)),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Export compliance audit events as CSV or JSON format for enterprise auditing."""
    query = select(AuditEvent).where(AuditEvent.organization_id == org.id)
    query = TenantQueryScoper.apply_scope(query, AuditEvent)

    if event_type:
        query = query.where(AuditEvent.event_type.ilike(f"%{event_type.strip()}%"))
    if resource_type:
        query = query.where(AuditEvent.resource_type.ilike(f"%{resource_type.strip()}%"))

    query = query.order_by(desc(AuditEvent.created_at)).limit(limit)
    result = await db.execute(query)
    rows = result.scalars().all()

    if format == "json":
        items = [AuditEventResponse.model_validate(r).model_dump(mode="json") for r in rows]
        return Response(
            content=json.dumps(items, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=audit-export-{org.slug}.json"},
        )

    # Default CSV Export with CSV formula injection defense
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp (UTC)", "Event ID", "Actor Type", "Actor ID", "Event Type", "Resource Type", "Resource ID", "Metadata"])

    def sanitize_csv_cell(val: Any) -> str:
        s = str(val or "")
        if s.startswith(("=", "+", "-", "@", "\t", "\r")):
            return f"'{s}"
        return s

    for row in rows:
        writer.writerow([
            sanitize_csv_cell(row.created_at.isoformat()),
            sanitize_csv_cell(str(row.id)),
            sanitize_csv_cell(row.actor_type.value if hasattr(row.actor_type, "value") else str(row.actor_type)),
            sanitize_csv_cell(str(row.actor_id or "")),
            sanitize_csv_cell(row.event_type),
            sanitize_csv_cell(row.resource_type),
            sanitize_csv_cell(str(row.resource_id or "")),
            sanitize_csv_cell(json.dumps(row.metadata_json or {})),
        ])

    csv_data = output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=audit-export-{org.slug}.csv"},
    )
