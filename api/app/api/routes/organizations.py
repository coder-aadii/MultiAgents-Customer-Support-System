from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.dependencies import get_current_user, require_admin
from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.models.user import User
from app.schemas.organizations import (
    MemberInviteRequest,
    MemberResponse,
    OrganizationCreateRequest,
    OrganizationResponse,
    OrganizationUpdateRequest,
)
from app.services.org_service import OrgService
from app.tenancy.rbac import require_org_role
from app.tenancy.resolver import resolve_tenant

orgs_router = APIRouter(prefix="/admin/organizations", tags=["admin-organizations"])


@orgs_router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def provision_organization(
    payload: OrganizationCreateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> OrganizationResponse:
    """Platform admins can provision new tenant organizations."""
    org = await OrgService.create_organization(
        db=db,
        name=payload.name,
        slug=payload.slug,
        creator_user_id=current_user.id,
        settings_json=payload.settings,
    )
    await db.commit()
    return OrganizationResponse(
        id=org.id,
        slug=org.slug,
        name=org.name,
        is_active=org.is_active,
        sso_config_json=org.sso_config_json,
        settings_json=org.settings_json,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


@orgs_router.get("/current", response_model=OrganizationResponse)
async def get_current_organization(
    org: Organization = Depends(resolve_tenant),
) -> OrganizationResponse:
    """Get active tenant organization profile and configuration."""
    return OrganizationResponse(
        id=org.id,
        slug=org.slug,
        name=org.name,
        is_active=org.is_active,
        sso_config_json=org.sso_config_json,
        settings_json=org.settings_json,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


@orgs_router.patch("/current", response_model=OrganizationResponse)
async def update_current_organization(
    payload: OrganizationUpdateRequest,
    org: Organization = Depends(resolve_tenant),
    _membership: OrganizationMembership = Depends(require_org_role(OrgMemberRole.admin, OrgMemberRole.owner)),
    db: AsyncSession = Depends(get_session),
) -> OrganizationResponse:
    """Update current tenant settings, branding, or SSO configuration."""
    if payload.name:
        org.name = payload.name.strip()
    if payload.settings is not None:
        org.settings_json = payload.settings
    if payload.sso_config is not None:
        # SSO configuration requires owner role
        if _membership.role != OrgMemberRole.owner.value:
            from app.common.errors import ForbiddenError
            raise ForbiddenError("Configuring Enterprise SSO requires Organization Owner role.")
        org.sso_config_json = payload.sso_config

    await db.commit()
    return OrganizationResponse(
        id=org.id,
        slug=org.slug,
        name=org.name,
        is_active=org.is_active,
        sso_config_json=org.sso_config_json,
        settings_json=org.settings_json,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


@orgs_router.post("/current/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def invite_member(
    payload: MemberInviteRequest,
    org: Organization = Depends(resolve_tenant),
    _membership: OrganizationMembership = Depends(require_org_role(OrgMemberRole.admin, OrgMemberRole.owner)),
    db: AsyncSession = Depends(get_session),
) -> MemberResponse:
    """Invite or add staff member to active organization."""
    user, membership = await OrgService.add_member(
        db=db,
        org_id=org.id,
        email=payload.email,
        display_name=payload.display_name,
        role=payload.role,
    )
    await db.commit()
    return MemberResponse(
        id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        role=membership.role,
        created_at=membership.created_at,
    )


@orgs_router.delete("/current/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def remove_member(
    user_id: uuid.UUID,
    org: Organization = Depends(resolve_tenant),
    _membership: OrganizationMembership = Depends(require_org_role(OrgMemberRole.admin, OrgMemberRole.owner)),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Remove member from active organization."""
    await OrgService.remove_member(db=db, org_id=org.id, user_id=user_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
