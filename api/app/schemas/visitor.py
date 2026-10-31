from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WidgetSessionRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)


class WidgetSessionResponse(BaseModel):
    session_token: str
    visitor_id: uuid.UUID
    conversation_id: uuid.UUID
    expires_at: datetime


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
