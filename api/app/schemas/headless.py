from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CustomerSessionExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_identity_token: str | None = Field(
        default=None,
        description="Cryptographically signed customer JWT verified against tenant secret or public key",
    )
    channel: str = Field(
        default="api",
        description="Client channel: 'api', 'mobile_ios', 'mobile_android', or 'web_widget'",
    )
    device_fingerprint: str | None = Field(
        default=None,
        description="Optional client device fingerprint for audit tracing",
    )


class CustomerSessionExchangeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_token: str
    expires_at: datetime
    identity_assurance_level: int
    verified_customer_id: str | None = None
    active_conversation_id: str


class HeadlessMessageCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str = Field(..., min_length=1, max_length=10000)
    client_message_id: str | None = Field(default=None, max_length=64)


class HeadlessConversationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    status: str
    title: str | None = None
    created_at: datetime
    last_message_at: datetime

