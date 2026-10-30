from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class WidgetSettingsResponse(BaseModel):
    id: uuid.UUID
    allowed_origin: str
    welcome_message: str
    accent_color: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class WidgetSettingsUpdate(BaseModel):
    allowed_origin: str | None = Field(default=None, min_length=1, max_length=2048)
    welcome_message: str | None = Field(default=None, min_length=1)
    accent_color: str | None = Field(default=None, min_length=4, max_length=7)
