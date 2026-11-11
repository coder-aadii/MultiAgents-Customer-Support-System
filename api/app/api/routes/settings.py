from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.models.widget_settings import WidgetSettings
from app.schemas.settings import WidgetSettingsResponse, WidgetSettingsUpdate
from app.tenancy.filter import TenantQueryScoper
from app.tenancy.rbac import require_org_role
from app.tenancy.resolver import resolve_tenant

settings_router = APIRouter(prefix="/settings", tags=["settings"])


@settings_router.get("/widget", response_model=WidgetSettingsResponse)
async def get_widget_settings(
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
) -> WidgetSettingsResponse:
    """Retrieve the active organization's widget customization and origin settings."""
    stmt = select(WidgetSettings).where(WidgetSettings.organization_id == org.id)
    stmt = TenantQueryScoper.apply_scope(stmt, WidgetSettings)
    res = await db.execute(stmt)
    widget_settings = res.scalars().first()

    if not widget_settings:
        # Create default widget settings singleton for this tenant
        widget_settings = WidgetSettings(
            id=uuid.uuid4(),
            organization_id=org.id,
            allowed_origin="http://localhost:5174",
            welcome_message="Hi! How can MCSS Support help today?",
            accent_color="#2563eb",
            updated_at=datetime.now(UTC),
        )
        db.add(widget_settings)
        await db.commit()
        await db.refresh(widget_settings)

    return WidgetSettingsResponse.model_validate(widget_settings)


@settings_router.patch("/widget", response_model=WidgetSettingsResponse)
async def update_widget_settings(
    payload: WidgetSettingsUpdate,
    org: Organization = Depends(resolve_tenant),
    _membership: OrganizationMembership = Depends(
        require_org_role(OrgMemberRole.admin, OrgMemberRole.owner)
    ),
    db: AsyncSession = Depends(get_session),
) -> WidgetSettingsResponse:
    """Update widget accent colors, welcome message, and allowed origins."""
    stmt = select(WidgetSettings).where(WidgetSettings.organization_id == org.id).with_for_update()
    stmt = TenantQueryScoper.apply_scope(stmt, WidgetSettings)
    res = await db.execute(stmt)
    widget_settings = res.scalars().first()

    if not widget_settings:
        widget_settings = WidgetSettings(
            id=uuid.uuid4(),
            organization_id=org.id,
            allowed_origin="http://localhost:5174",
            welcome_message="Hi! How can MCSS Support help today?",
            accent_color="#2563eb",
            updated_at=datetime.now(UTC),
        )
        db.add(widget_settings)

    if payload.allowed_origin is not None:
        widget_settings.allowed_origin = payload.allowed_origin.strip()
    if payload.welcome_message is not None:
        widget_settings.welcome_message = payload.welcome_message.strip()
    if payload.accent_color is not None:
        widget_settings.accent_color = payload.accent_color.strip()

    widget_settings.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(widget_settings)

    return WidgetSettingsResponse.model_validate(widget_settings)

