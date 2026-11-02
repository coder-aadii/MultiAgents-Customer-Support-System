from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.actions.base import RiskLevel
from app.models.business_action import ActionStatus


class ActionApprovalRequest(BaseModel):
    notes: str | None = None


class ActionRejectionRequest(BaseModel):
    reason: str = Field(min_length=1, description="Documented reason for rejection")


class BusinessActionResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    customer_id: str
    action_name: str
    risk_level: str
    status: str
    idempotency_key: str
    payload_json: dict[str, Any]
    result_json: dict[str, Any] | None = None
    summary_message: str
    required_confirmation_phrase: str
    expires_at: datetime
    confirmed_at: datetime | None = None
    reviewed_by_user_id: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    review_notes: str | None = None
    execution_attempts: int
    created_at: datetime
    updated_at: datetime

