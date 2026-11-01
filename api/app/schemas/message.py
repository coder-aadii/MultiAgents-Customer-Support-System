from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.message import SenderType


HTML_TAG_REGEX = re.compile(r"</?[a-zA-Z][a-zA-Z0-9:-]*(?:\s+[^>]*)?/?>")


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    client_message_id: uuid.UUID

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Message content cannot be empty or whitespace only")

        # Strip valid HTML markup while preserving mathematical / logical operators (e.g. < and >)
        if HTML_TAG_REGEX.search(trimmed):
            cleaned = HTML_TAG_REGEX.sub("", trimmed).strip()
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
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="metadata_json",
        serialization_alias="metadata",
    )
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}
