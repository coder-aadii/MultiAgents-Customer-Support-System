from __future__ import annotations

import enum
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_action import ActionRiskLevel, ActionStatus, BusinessAction


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionDraft(BaseModel):
    """Structured proposal emitted to the customer requiring explicit confirmation."""

    model_config = ConfigDict(frozen=True)

    action_id: uuid.UUID
    action_name: str
    risk_level: RiskLevel
    summary_message: str
    required_confirmation_phrase: str
    idempotency_key: str
    expires_at: datetime
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    """Result of an action execution mutation or cached idempotency response."""

    action_id: uuid.UUID
    status: ActionStatus
    success: bool
    confirmation_number: str | None = None
    message: str
    reconciled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = Field(default_factory=dict)


class ActionExecutionResponse(ActionResult):
    """API-facing action execution response."""

    pass


class ActionDefinition(ABC):
    """Base class for registering executable controlled business actions."""

    name: str
    risk_level: RiskLevel
    description: str

    @abstractmethod
    async def validate_preconditions(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        customer_id: str,
        parameters: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Validates business preconditions before an action draft is created.

        Returns:
            (is_eligible, failure_reason_or_summary, validated_parameters)
        """
        ...

    @abstractmethod
    def generate_confirmation_phrase(
        self,
        customer_id: str,
        validated_parameters: dict[str, Any],
    ) -> tuple[str, str]:
        """Generates (summary_message, required_confirmation_phrase)."""
        ...

    @abstractmethod
    async def execute(
        self,
        db: AsyncSession,
        action: BusinessAction,
    ) -> ActionResult:
        """Executes the external or internal mutation idempotently."""
        ...

