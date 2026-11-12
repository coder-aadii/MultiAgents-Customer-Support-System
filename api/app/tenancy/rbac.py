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

# Fine-grained Enterprise Permissions
PERM_CONVERSATIONS_READ = "conversations:read"
PERM_CONVERSATIONS_WRITE = "conversations:write"
PERM_ACTIONS_APPROVE = "actions:approve"
PERM_KNOWLEDGE_PUBLISH = "knowledge:publish"
PERM_AUDIT_READ = "audit:read"
PERM_AUDIT_EXPORT = "audit:export"
PERM_CONNECTORS_MANAGE = "connectors:manage"
PERM_SETTINGS_MANAGE = "settings:manage"

ROLE_HIERARCHY: dict[str, int] = {
    OrgMemberRole.owner.value: 4,
    OrgMemberRole.admin.value: 3,
    OrgMemberRole.supervisor.value: 2,
    OrgMemberRole.agent.value: 1,
    OrgMemberRole.auditor.value: 1,
}

DEFAULT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    OrgMemberRole.owner.value: {"*"},
    OrgMemberRole.admin.value: {"*"},
    OrgMemberRole.supervisor.value: {
        PERM_CONVERSATIONS_READ,
        PERM_CONVERSATIONS_WRITE,
        PERM_ACTIONS_APPROVE,
        PERM_KNOWLEDGE_PUBLISH,
        PERM_AUDIT_READ,
    },
    OrgMemberRole.agent.value: {
        PERM_CONVERSATIONS_READ,
        PERM_CONVERSATIONS_WRITE,
    },
    OrgMemberRole.auditor.value: {
        PERM_AUDIT_READ,
        PERM_AUDIT_EXPORT,
        PERM_CONVERSATIONS_READ,
    },
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
            if current_user.role == UserRole.admin:
                return OrganizationMembership(
                    organization_id=org.id,
                    user_id=current_user.id,
                    role=OrgMemberRole.owner.value,
                    permissions=["*"],
                )
            raise ForbiddenError(f"User is not a member of organization '{org.slug}'")

        if current_user.role == UserRole.admin:
            user_level = ROLE_HIERARCHY.get(OrgMemberRole.owner.value, 4)
        else:
            user_level = ROLE_HIERARCHY.get(membership.role, 0)
        min_level = min(ROLE_HIERARCHY.get(r.value, 1) for r in allowed_roles)

        if user_level < min_level:
            allowed_names = [r.value for r in allowed_roles]
            raise ForbiddenError(
                f"Requires one of {allowed_names}, but user has role '{membership.role}'."
            )
        return membership

    return _checker


def require_permission(permission: str) -> Callable:
    """Dependency enforcing fine-grained enterprise permission grants."""

    async def _perm_checker(
        current_user: User = Depends(get_current_user),
        org: Organization = Depends(resolve_tenant),
        db: AsyncSession = Depends(get_session),
    ) -> OrganizationMembership:
        # System-level admins bypass tenant membership checks
        if current_user.role == UserRole.admin:
            return OrganizationMembership(
                organization_id=org.id,
                user_id=current_user.id,
                role=OrgMemberRole.owner.value,
                permissions=["*"],
            )

        membership = await OrgService.get_membership(db, org.id, current_user.id)
        if not membership:
            raise ForbiddenError(f"User is not a member of organization '{org.slug}'")

        # Resolve explicit permissions combined with default role permissions
        user_perms = set(membership.permissions or [])
        default_perms = DEFAULT_ROLE_PERMISSIONS.get(membership.role, set())
        effective_perms = user_perms.union(default_perms)

        if "*" not in effective_perms and permission not in effective_perms:
            raise ForbiddenError(
                f"Forbidden: action requires permission '{permission}', role '{membership.role}' lacks grant."
            )

        return membership

    return _perm_checker
