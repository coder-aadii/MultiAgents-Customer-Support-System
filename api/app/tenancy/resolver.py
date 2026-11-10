from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import ForbiddenError, NotFoundError
from app.database import get_session
from app.models.organization import Organization
from app.models.user import User
from app.security.tokens import decode_access_jwt
from app.tenancy.context import DEFAULT_ORGANIZATION_ID, TenantContext


async def resolve_tenant(
    request: Request,
    db: AsyncSession = Depends(get_session),
    x_org_slug: str | None = Header(default=None, alias="X-Organization-Slug"),
    x_org_id: str | None = Header(default=None, alias="X-Organization-Id"),
    org_slug: str | None = Query(default=None, alias="organization_slug"),
) -> Organization:
    """Resolves active tenant organization binding authenticated identity first,
    then unauthenticated headers/params, or falling back to default.

    Security guarantees:
    - If a valid authenticated user JWT is presented, the authenticated identity's
      tenant claim is authoritative.
    - If client supplies conflicting X-Organization-Id or X-Organization-Slug headers
      that mismatch the verified token identity, a 403 Forbidden is raised.
    - Anonymous/public routes (e.g., widget session initiation) can select organization
      via header or query param. If omitted, default Olist demo tenant is used.
    """
    target_slug: str | None = x_org_slug or org_slug
    header_org_id: uuid.UUID | None = None
    if x_org_id:
        try:
            header_org_id = uuid.UUID(x_org_id)
        except ValueError:
            pass

    # 1. Inspect authenticated Authorization bearer token
    jwt_org_id: uuid.UUID | None = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        raw_token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = decode_access_jwt(raw_token)
            if "org_id" in payload and payload["org_id"]:
                jwt_org_id = uuid.UUID(str(payload["org_id"]))
            elif "sub" in payload:
                # If org_id claim omitted, look up user's authoritative organization
                try:
                    user_sub = uuid.UUID(str(payload["sub"]))
                    user_stmt = select(User.organization_id).where(User.id == user_sub)
                    user_res = await db.execute(user_stmt)
                    user_org_id = user_res.scalar_one_or_none()
                    if user_org_id:
                        jwt_org_id = user_org_id
                except (ValueError, TypeError):
                    pass
        except Exception:
            # Not a valid staff access JWT (e.g. opaque visitor token or malformed)
            pass

    # 2. If authenticated user identity is established, enforce strict tenant binding
    if jwt_org_id:
        if header_org_id and header_org_id != jwt_org_id:
            raise ForbiddenError("Tenant header mismatch with authenticated identity")

        stmt = select(Organization).where(Organization.id == jwt_org_id, Organization.is_active.is_(True))
        res = await db.execute(stmt)
        org = res.scalar_one_or_none()
        if not org:
            raise NotFoundError(f"Organization '{jwt_org_id}' not found or inactive")

        if target_slug and org.slug != target_slug:
            raise ForbiddenError("Tenant header mismatch with authenticated identity")

        TenantContext.set_tenant(org.id, org.slug)
        return org

    # 3. Unauthenticated/anonymous caller: resolve from explicit headers or query params
    if header_org_id:
        stmt = select(Organization).where(Organization.id == header_org_id, Organization.is_active.is_(True))
        res = await db.execute(stmt)
        org = res.scalar_one_or_none()
        if not org:
            raise NotFoundError(f"Organization '{header_org_id}' not found or inactive")
    elif target_slug:
        stmt = select(Organization).where(Organization.slug == target_slug, Organization.is_active.is_(True))
        res = await db.execute(stmt)
        org = res.scalar_one_or_none()
        if not org:
            raise NotFoundError(f"Organization slug '{target_slug}' not found or inactive")
    else:
        # 4. Fallback to default organization (e.g., Olist demo store)
        stmt = select(Organization).where(Organization.id == DEFAULT_ORGANIZATION_ID)
        res = await db.execute(stmt)
        org = res.scalar_one_or_none()
        if not org:
            org = Organization(
                id=DEFAULT_ORGANIZATION_ID,
                slug="default",
                name="Default Organization",
                is_active=True,
            )
            db.add(org)
            await db.commit()

    TenantContext.set_tenant(org.id, org.slug)
    return org

