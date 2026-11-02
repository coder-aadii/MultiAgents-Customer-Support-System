from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import NotFoundError
from app.database import get_session
from app.models.organization import Organization
from app.tenancy.sso import SSOError, SSOHandler

sso_router = APIRouter(prefix="/auth/sso", tags=["auth-sso"])


class SSOCallbackPayload(BaseModel):
    SAMLResponse: str | None = None
    id_token: str | None = None
    code: str | None = None
    claims: dict[str, Any] | None = None


@sso_router.get("/{org_slug}/metadata", response_class=Response)
async def get_sp_metadata(
    org_slug: str,
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Expose SAML 2.0 SP metadata XML for enterprise IdP trust federation."""
    stmt = select(Organization).where(Organization.slug == org_slug, Organization.is_active.is_(True))
    res = await db.execute(stmt)
    org = res.scalar_one_or_none()
    if not org:
        raise NotFoundError(f"Organization '{org_slug}' not found")

    metadata_xml = SSOHandler.generate_sp_metadata(org)
    return Response(content=metadata_xml, media_type="application/xml")


@sso_router.get("/{org_slug}/login")
async def initiate_sso_login(
    org_slug: str,
    relay_state: str | None = Query(default=None),
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Returns the IdP redirect URL for single sign-on."""
    stmt = select(Organization).where(Organization.slug == org_slug, Organization.is_active.is_(True))
    res = await db.execute(stmt)
    org = res.scalar_one_or_none()
    if not org:
        raise NotFoundError(f"Organization '{org_slug}' not found")

    try:
        login_url = SSOHandler.get_login_url(org, relay_state=relay_state)
        return {"redirect_url": login_url}
    except SSOError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@sso_router.post("/{org_slug}/callback")
async def sso_callback(
    org_slug: str,
    payload: SSOCallbackPayload,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Ingest SAML assertion or OIDC claims, provision user, and return JWT token."""
    stmt = select(Organization).where(Organization.slug == org_slug, Organization.is_active.is_(True))
    res = await db.execute(stmt)
    org = res.scalar_one_or_none()
    if not org:
        raise NotFoundError(f"Organization '{org_slug}' not found")

    try:
        if payload.SAMLResponse:
            user, membership, token = await SSOHandler.process_saml_assertion(
                db=db,
                org=org,
                assertion_xml_or_b64=payload.SAMLResponse,
            )
        elif payload.claims:
            user, membership, token = await SSOHandler.process_oidc_claims(
                db=db,
                org=org,
                claims=payload.claims,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing SAMLResponse or claims in SSO callback",
            )

        await db.commit()
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": str(user.id),
                "email": user.email,
                "display_name": user.display_name,
                "role": user.role.value,
            },
            "organization": {
                "id": str(org.id),
                "slug": org.slug,
                "name": org.name,
                "membership_role": membership.role,
            },
        }
    except SSOError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

