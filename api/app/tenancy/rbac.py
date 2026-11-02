from __future__ import annotations

from typing import Callable

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import ForbiddenError
from app.database import get_session
from app.dependencies import get_current_user
from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.models.user import User, UserRole
from app.services.org_service import OrgService
from app.tenancy.resolver import resolve_tenant

ROLE_HIERARCHY: dict[str, int] = {
    OrgMemberRole.owner.value: 4,
    OrgMemberRole.admin.value: 3,
    OrgMemberRole.supervisor.value: 2,
    OrgMemberRole.agent.value: 1,
}


def require_org_role(*allowed_roles: OrgMemberRole) -> Callable:
    """Dependency enforcing role hierarchy checks within the active organization."""

    async def _checker(
        current_user: User = Depends(get_current_user),
        org: Organization = Depends(resolve_tenant),
        db: AsyncSession = Depends(get_session),
    ) -> OrganizationMembership:
        membership = await OrgService.get_membership(db, org.id, current_user.id)
        if not membership:
            raise ForbiddenError(f"User is not a member of organization '{org.slug}'")

        user_level = ROLE_HIERARCHY.get(membership.role, 0)
        min_level = min(ROLE_HIERARCHY.get(r.value, 1) for r in allowed_roles)

        if user_level < min_level:
            allowed_names = [r.value for r in allowed_roles]
            raise ForbiddenError(
                f"Requires one of {allowed_names}, but user has role '{membership.role}'."
            )
        return membership

    return _checker

