from __future__ import annotations

import enum
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DeliveryStatus(str, enum.Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class ChannelEvent(BaseModel):
    """Canonical normalized envelope for all inbound and outbound omnichannel events."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    organization_id: uuid.UUID
    channel_type: str  # 'email', 'whatsapp', 'sms', 'mobile', 'social', 'voice'
    external_user_id: str  # e.g., phone '+1234567890' or email 'user@example.com'
    external_conversation_id: str | None = None
    external_message_id: str
    direction: Literal["inbound", "outbound"]
    payload_type: Literal["text", "rich_card", "image", "document", "location", "audio"] = "text"
    text_content: str
    media_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DeliveryResult(BaseModel):
    """Standard delivery result returned after dispatching an outbound channel message."""

    model_config = ConfigDict(frozen=True)

    success: bool
    channel_type: str
    external_message_id: str | None = None
    delivery_status: DeliveryStatus = DeliveryStatus.SENT
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BaseChannelAdapter(ABC):
    """Universal contract for channel providers (Email, WhatsApp, SMS, Social, Voice)."""

    @property
    @abstractmethod
    def channel_type(self) -> str:
        """Normalized channel identifier string."""
        ...

    @abstractmethod
    async def verify_webhook_signature(
        self,
        request_headers: dict[str, str],
        raw_body: bytes,
        secret: str,
    ) -> bool:
        """Cryptographically verifies inbound webhook signature against channel secret."""
        ...

    @abstractmethod
    async def parse_inbound_event(
        self,
        raw_payload: dict[str, Any],
        organization_id: uuid.UUID,
    ) -> ChannelEvent:
        """Parses vendor-specific JSON payload into canonical ChannelEvent."""
        ...

    @abstractmethod
    async def send_outbound_message(
        self,
        event: ChannelEvent,
        credentials: dict[str, Any],
    ) -> DeliveryResult:
        """Dispatches an outbound message to provider API."""
        ...

