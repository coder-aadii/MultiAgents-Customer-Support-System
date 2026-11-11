from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=2, max_length=64)
    connector_type: str = Field(default="rest", pattern="^(rest|sql|graphql)$")
    is_enabled: bool = True
    base_url: str | None = Field(default=None, max_length=255)
    config_json: dict[str, Any] = Field(default_factory=dict)
    auth_type: str = Field(default="bearer", pattern="^(bearer|basic|api_key|mtls|none)$")
    secret_reference: str = Field(default="NONE", max_length=128)


class ConnectorUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=2, max_length=64)
    connector_type: str | None = Field(default=None, pattern="^(rest|sql|graphql)$")
    is_enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=255)
    config_json: dict[str, Any] | None = None
    auth_type: str | None = Field(default=None, pattern="^(bearer|basic|api_key|mtls|none)$")
    secret_reference: str | None = Field(default=None, max_length=128)


class ConnectorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    connector_type: str
    is_enabled: bool
    base_url: str | None = None
    config_json: dict[str, Any] = Field(default_factory=dict)
    auth_type: str
    secret_reference: str
    created_at: datetime
    updated_at: datetime


class ConnectorTestRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_url: str = Field(..., max_length=255)
    auth_type: str = Field(default="none", pattern="^(bearer|basic|api_key|mtls|none)$")
    secret_reference: str = Field(default="NONE", max_length=128)


class ConnectorTestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success: bool
    message: str
    latency_ms: float = 0.0
    status_code: int | None = None
    error_type: str | None = None

