from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.organization import OrgMemberRole


class OrganizationCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    slug: str = Field(min_length=2, max_length=64)
    settings: dict[str, Any] = Field(default_factory=dict)


class OrganizationUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    settings: dict[str, Any] | None = None
    sso_config: dict[str, Any] | None = None


class MemberInviteRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=100)
    role: OrgMemberRole = OrgMemberRole.agent


class OrganizationResponse(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    is_active: bool
    sso_config_json: dict[str, Any] | None = None
    settings_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MemberResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    created_at: datetime
