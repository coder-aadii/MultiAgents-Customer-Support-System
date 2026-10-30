from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.handoff import HandoffReason, HandoffStatus


class HandoffResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    reason: HandoffReason
    summary: str
    status: HandoffStatus
    claimed_by_user_id: uuid.UUID | None
    created_at: datetime
    claimed_at: datetime | None
    resolved_at: datetime | None

    model_config = {"from_attributes": True}
