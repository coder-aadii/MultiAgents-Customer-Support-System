from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.audit_event import ActorType


class AuditEventResponse(BaseModel):
    id: uuid.UUID
    actor_type: ActorType
    actor_id: uuid.UUID | None
    event_type: str
    resource_type: str
    resource_id: uuid.UUID | None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}
