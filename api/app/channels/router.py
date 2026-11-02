from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.adapters.whatsapp import WhatsAppChannelAdapter
from app.channels.registry import default_channel_registry
from app.common.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.database import get_session
from app.dependencies import get_current_user, require_admin
from app.models.channel_configuration import ChannelConfiguration
from app.models.organization import Organization
from app.models.user import User
from app.tenancy.context import TenantContext

channels_router = APIRouter(tags=["channels"])


class ChannelConfigCreateRequest(BaseModel):
    channel_type: str = Field(..., max_length=32)
    webhook_secret: str = Field(..., min_length=8, max_length=255)
    is_enabled: bool = True
    api_credentials: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelConfigResponse(BaseModel):
    id: uuid.UUID
    channel_type: str
    is_enabled: bool
    created_at: Any


@channels_router.get("/webhooks/channels/whatsapp/{org_slug}")
async def whatsapp_webhook_challenge(
    org_slug: str,
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Handles Meta WhatsApp Cloud API initial subscription verification handshake."""
    stmt = select(Organization).where(Organization.slug == org_slug, Organization.is_active.is_(True))
    res = await db.execute(stmt)
    org = res.scalar_one_or_none()
    if not org:
        raise NotFoundError("Organization not found")

    cfg_stmt = select(ChannelConfiguration).where(
        ChannelConfiguration.organization_id == org.id,
        ChannelConfiguration.channel_type == "whatsapp",
    )
    cfg_res = await db.execute(cfg_stmt)
    config = cfg_res.scalar_one_or_none()

    secret = config.webhook_secret_enc if config else "default_token"
    wa_adapter = WhatsAppChannelAdapter()
    challenge = wa_adapter.verify_webhook_challenge(hub_mode, hub_verify_token, hub_challenge, secret)
    if challenge:
        return Response(content=challenge, media_type="text/plain")

    raise UnauthorizedError("Invalid verify token or handshake mode")


@channels_router.post("/webhooks/channels/{channel_type}/{org_slug}")
async def receive_channel_webhook(
    channel_type: str,
    org_slug: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Ingests inbound channel messages, verifies signatures, deduplicates, and creates conversation."""
    raw_body = await request.body()
    headers = dict(request.headers)

    parsed_json = None
    content_type = headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            parsed_json = await request.json()
        except Exception:
            parsed_json = None
    elif "application/x-www-form-urlencoded" in content_type:
        form_data = await request.form()
        parsed_json = dict(form_data)

    return await default_channel_registry.handle_inbound_webhook(
        db=db,
        channel_type=channel_type.lower().strip(),
        org_slug=org_slug,
        headers=headers,
        raw_body=raw_body,
        parsed_json=parsed_json,
    )


@channels_router.get("/admin/channels", response_model=list[ChannelConfigResponse])
async def list_channel_configurations(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> list[ChannelConfigResponse]:
    """Lists configured channels for current organization."""
    org_id = TenantContext.get_tenant_id()
    stmt = select(ChannelConfiguration).where(ChannelConfiguration.organization_id == org_id)
    res = await db.execute(stmt)
    configs = res.scalars().all()
    return [
        ChannelConfigResponse(
            id=c.id,
            channel_type=c.channel_type,
            is_enabled=c.is_enabled,
            created_at=c.created_at,
        )
        for c in configs
    ]


@channels_router.post("/admin/channels", response_model=ChannelConfigResponse)
async def create_or_update_channel(
    payload: ChannelConfigCreateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> ChannelConfigResponse:
    """Configures a communication channel for the organization."""
    org_id = TenantContext.get_tenant_id()
    stmt = select(ChannelConfiguration).where(
        ChannelConfiguration.organization_id == org_id,
        ChannelConfiguration.channel_type == payload.channel_type.lower().strip(),
    )
    res = await db.execute(stmt)
    config = res.scalar_one_or_none()

    if not config:
        config = ChannelConfiguration(
            organization_id=org_id,
            channel_type=payload.channel_type.lower().strip(),
            webhook_secret_enc=payload.webhook_secret,
            is_enabled=payload.is_enabled,
            api_credentials_json=payload.api_credentials,
            channel_metadata_json=payload.metadata,
        )
        db.add(config)
    else:
        config.webhook_secret_enc = payload.webhook_secret
        config.is_enabled = payload.is_enabled
        config.api_credentials_json = payload.api_credentials
        config.channel_metadata_json = payload.metadata

    await db.commit()
    await db.refresh(config)
    return ChannelConfigResponse(
        id=config.id,
        channel_type=config.channel_type,
        is_enabled=config.is_enabled,
        created_at=config.created_at,
    )

