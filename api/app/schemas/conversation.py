from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.conversation import ConversationStatus
from app.schemas.user import UserResponse
from app.schemas.visitor import VisitorResponse


class ConversationResponse(BaseModel):
    id: uuid.UUID
    visitor_id: uuid.UUID
    status: ConversationStatus
    assigned_user_id: uuid.UUID | None
    title: str | None
    last_message_at: datetime
    created_at: datetime
    updated_at: datetime
    priority: str = "normal"
    sla_breached: bool = False
    channel_type: str | None = None
    assigned_team_id: uuid.UUID | None = None
    assigned_agent_id: uuid.UUID | None = None
    assignee: UserResponse | None = None
    visitor: VisitorResponse | None = None

    model_config = {"from_attributes": True}


class ConversationDetailResponse(ConversationResponse):
    pass
