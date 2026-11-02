from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import NotFoundError
from app.database import get_session
from app.models.organization import Organization
from app.security.tokens import decode_access_jwt
from app.tenancy.context import DEFAULT_ORGANIZATION_ID, TenantContext


async def resolve_tenant(
    request: Request,
    db: AsyncSession = Depends(get_session),
    x_org_slug: str | None = Header(default=None, alias="X-Organization-Slug"),
    x_org_id: str | None = Header(default=None, alias="X-Organization-Id"),
    org_slug: str | None = Query(default=None, alias="organization_slug"),
) -> Organization:
    """Resolves active tenant organization from headers, auth token, query params, or default."""
    target_id: uuid.UUID | None = None
    target_slug: str | None = x_org_slug or org_slug

    # 1. Check direct ID header
    if x_org_id:
        try:
            target_id = uuid.UUID(x_org_id)
        except ValueError:
            pass

    # 2. Check JWT Authorization bearer token
    if not target_id and not target_slug:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            raw_token = auth_header.split(" ", 1)[1].strip()
            try:
                payload = decode_access_jwt(raw_token)
                if "org_id" in payload:
                    target_id = uuid.UUID(payload["org_id"])
            except Exception:
                pass

    # 3. Lookup Organization in DB
    if target_id:
        stmt = select(Organization).where(Organization.id == target_id, Organization.is_active.is_(True))
        res = await db.execute(stmt)
        org = res.scalar_one_or_none()
        if not org:
            raise NotFoundError(f"Organization '{target_id}' not found or inactive")
    elif target_slug:
        stmt = select(Organization).where(Organization.slug == target_slug, Organization.is_active.is_(True))
        res = await db.execute(stmt)
        org = res.scalar_one_or_none()
        if not org:
            raise NotFoundError(f"Organization slug '{target_slug}' not found or inactive")
    else:
        # 4. Fallback to default organization
        stmt = select(Organization).where(Organization.id == DEFAULT_ORGANIZATION_ID)
        res = await db.execute(stmt)
        org = res.scalar_one_or_none()
        if not org:
            # Create if missing
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

