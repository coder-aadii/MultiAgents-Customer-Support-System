from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import ConflictError, ForbiddenError, NotFoundError
from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.models.user import User, UserRole
from app.security.password import hash_password

logger = logging.getLogger("mcss.tenancy.org_service")


class OrgService:
    """Manages organization provisioning, membership lifecycles, and RBAC governance."""

    @classmethod
    async def get_membership(
        cls,
        db: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> OrganizationMembership | None:
        stmt = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user_id,
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none()

    @classmethod
    async def create_organization(
        cls,
        db: AsyncSession,
        name: str,
        slug: str,
        creator_user_id: uuid.UUID | None = None,
        settings_json: dict[str, Any] | None = None,
    ) -> Organization:
        clean_slug = slug.strip().lower()
        stmt = select(Organization).where(Organization.slug == clean_slug)
        res = await db.execute(stmt)
        if res.scalar_one_or_none():
            raise ConflictError(f"Organization slug '{clean_slug}' already exists")

        now = datetime.now(UTC)
        org = Organization(
            name=name.strip(),
            slug=clean_slug,
            is_active=True,
            settings_json=settings_json or {},
            created_at=now,
            updated_at=now,
        )
        db.add(org)
        await db.flush()

        if creator_user_id:
            membership = OrganizationMembership(
                organization_id=org.id,
                user_id=creator_user_id,
                role=OrgMemberRole.owner.value,
                permissions=[],
                created_at=now,
            )
            db.add(membership)
            await db.flush()

        return org

    @classmethod
    async def add_member(
        cls,
        db: AsyncSession,
        org_id: uuid.UUID,
        email: str,
        display_name: str,
        role: OrgMemberRole = OrgMemberRole.agent,
    ) -> tuple[User, OrganizationMembership]:
        clean_email = email.strip().lower()
        now = datetime.now(UTC)

        # Find or create user
        stmt = select(User).where(User.email == clean_email)
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()

        if not user:
            user = User(
                email=clean_email,
                organization_id=org_id,
                password_hash=hash_password(uuid.uuid4().hex),
                display_name=display_name.strip(),
                role=UserRole.admin if role in (OrgMemberRole.owner, OrgMemberRole.admin) else UserRole.staff,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
            await db.flush()

        # Check existing membership
        existing_mem = await cls.get_membership(db, org_id, user.id)
        if existing_mem:
            raise ConflictError(f"User {clean_email} is already a member of this organization")

        membership = OrganizationMembership(
            organization_id=org_id,
            user_id=user.id,
            role=role.value,
            permissions=[],
            created_at=now,
        )
        db.add(membership)
        await db.flush()
        return user, membership

    @classmethod
    async def remove_member(
        cls,
        db: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        mem = await cls.get_membership(db, org_id, user_id)
        if not mem:
            raise NotFoundError("Membership not found in this organization")

        if mem.role == OrgMemberRole.owner.value:
            # Check if this is the last owner
            owner_stmt = select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.role == OrgMemberRole.owner.value,
            )
            res = await db.execute(owner_stmt)
            owners = list(res.scalars().all())
            if len(owners) <= 1:
                raise ForbiddenError("Cannot remove the sole organization owner")

        await db.delete(mem)
        await db.flush()
        return True

