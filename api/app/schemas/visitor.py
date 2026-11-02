from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WidgetSessionRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    customer_token: str | None = Field(
        default=None,
        description="Optional upstream customer JWT token (can also be passed in x-customer-token header)",
    )


class WidgetSessionResponse(BaseModel):
    session_token: str
    visitor_token: str | None = Field(
        default=None,
        description="Alias for session_token for API specification consistency",
    )
    visitor_id: uuid.UUID
    conversation_id: uuid.UUID
    expires_at: datetime
    identity_assurance_level: int = Field(
        default=0,
        description="Identity assurance level: 0 (Anonymous), 1 (Claimed), 2 (Cryptographically Verified)",
    )
    verified_customer_id: str | None = Field(
        default=None,
        description="Verified host system customer ID if authenticated (IAL 2)",
    )


class VisitorProfileUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)


class VisitorResponse(BaseModel):
    id: uuid.UUID
    name: str | None = None
    email: str | None = None
    created_at: datetime
    last_seen_at: datetime

    model_config = ConfigDict(from_attributes=True)
