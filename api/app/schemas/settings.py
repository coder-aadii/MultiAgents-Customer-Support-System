from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("allowed_origin")
    @classmethod
    def validate_allowed_origin(cls, v: str | None) -> str | None:
        if v is not None:
            clean = v.strip()
            if clean == "*":
                raise ValueError("Wildcard '*' allowed_origin is not permitted.")
            if not (clean.startswith("http://") or clean.startswith("https://")):
                raise ValueError("allowed_origin must start with http:// or https://")
            return clean
        return None

    @field_validator("accent_color")
    @classmethod
    def validate_accent_color(cls, v: str | None) -> str | None:
        if v is not None:
            clean = v.strip()
            if not re.match(r"^#[0-9a-fA-F]{3,6}$", clean):
                raise ValueError("accent_color must be a valid hex color code (e.g. #2563eb).")
            return clean
        return None
