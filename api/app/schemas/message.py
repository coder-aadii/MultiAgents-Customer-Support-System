from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.message import SenderType


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    client_message_id: uuid.UUID

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Message content cannot be empty or whitespace only")
        if "<" in trimmed and ">" in trimmed:
            cleaned = re.sub(r"<[^>]+>", "", trimmed).strip()
            if not cleaned:
                raise ValueError("Message content cannot be HTML only")
            return cleaned
        return trimmed


class MessageResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    client_message_id: uuid.UUID | None
    sender_type: SenderType
    sender_user_id: uuid.UUID | None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}
