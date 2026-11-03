"""Typed contracts shared by Phase 6 agents and their audit trail."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentExecutionStatus(StrEnum):
    success = "success"
    budget_exceeded = "budget_exceeded"
    failed = "failed"
    aborted = "aborted"


class AgentTokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt: int = Field(default=0, ge=0)
    completion: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def total_matches_components(self) -> AgentTokenUsage:
        if self.total != self.prompt + self.completion:
            raise ValueError("total token usage must equal prompt plus completion")
        return self


class AgentExecutionInput(BaseModel):
    """Server-authenticated input passed to an agent execution."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    execution_phase: str = Field(min_length=1, max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)
    verified_customer_id: str | None = Field(default=None, max_length=128)


class AgentTraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str = Field(min_length=1, max_length=64)
    details: dict[str, Any] = Field(default_factory=dict)


class AgentExecutionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AgentExecutionStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    token_usage: AgentTokenUsage = Field(default_factory=AgentTokenUsage)
    tool_calls: int = Field(default=0, ge=0)
    trace: list[AgentTraceEvent] = Field(default_factory=list)
    error_code: str | None = Field(default=None, max_length=64)

    @field_validator("trace")
    @classmethod
    def trace_is_bounded(cls, trace: list[AgentTraceEvent]) -> list[AgentTraceEvent]:
        if len(trace) > 100:
            raise ValueError("agent trace may contain at most 100 events")
        return trace


class AgentDefinition(BaseModel):
    """Immutable registry metadata; prompts remain code-owned, never client input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(min_length=1, max_length=16)
    capabilities: tuple[str, ...]
    tool_allowlist: frozenset[str]
