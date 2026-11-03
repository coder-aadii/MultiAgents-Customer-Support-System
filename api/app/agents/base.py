"""Safe base abstractions for the Phase 6 agent framework."""

from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agents.schemas import AgentExecutionStatus, AgentTokenUsage, AgentTraceEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.visitor_session import VisitorSession


class TenantSecurityError(ValueError):
    """Raised when an agent is created without server-authenticated tenant scope."""


class AgentBudgetExceededError(RuntimeError):
    """Raised when an agent exceeds its finite execution budget."""


@dataclass(frozen=True, slots=True)
class AgentBudget:
    max_execution_tokens: int = 2048
    max_tool_calls: int = 3
    max_timeout_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.max_execution_tokens < 1:
            raise ValueError("max_execution_tokens must be positive")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls cannot be negative")
        if self.max_timeout_seconds <= 0:
            raise ValueError("max_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Authenticated, server-created execution context for an individual agent."""

    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    execution_phase: str
    payload: dict[str, Any] = field(default_factory=dict)
    verified_customer_id: str | None = None
    budget: AgentBudget = field(default_factory=AgentBudget)
    db: AsyncSession | None = None
    visitor_session: VisitorSession | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, uuid.UUID):
            raise TenantSecurityError("AgentContext requires a valid tenant_id")
        if not isinstance(self.conversation_id, uuid.UUID):
            raise ValueError("AgentContext requires a valid conversation_id")
        if not self.execution_phase or len(self.execution_phase) > 32:
            raise ValueError("execution_phase must be between 1 and 32 characters")


@dataclass(frozen=True, slots=True)
class AgentResult:
    status: AgentExecutionStatus = AgentExecutionStatus.success
    payload: dict[str, Any] = field(default_factory=dict)
    token_usage: AgentTokenUsage = field(default_factory=AgentTokenUsage)
    tool_calls: int = 0
    trace: tuple[AgentTraceEvent, ...] = ()
    error_code: str | None = None


class BaseAgent(ABC):
    """Base class for independently auditable, bounded specialist agents."""

    agent_name = "base_agent"
    agent_version = "1"
    system_prompt = ""  # Subclasses must use versioned code constants, never request data.

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """Perform the agent's domain work. Implementations must remain side-effect free in 6A."""

    @abstractmethod
    def get_capabilities(self) -> list[str]:
        """Return capabilities exclusively owned by this agent."""

    @abstractmethod
    def get_tool_allowlist(self) -> set[str]:
        """Return the registered read-only tools this agent may request in later phases."""

    async def execute(self, context: AgentContext) -> AgentResult:
        """Run with deterministic time, token, and tool-call budget enforcement."""
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(self.run(context), timeout=context.budget.max_timeout_seconds)
        except TimeoutError as exc:
            raise AgentBudgetExceededError("agent execution exceeded its timeout budget") from exc

        if time.monotonic() - started > context.budget.max_timeout_seconds:
            raise AgentBudgetExceededError("agent execution exceeded its timeout budget")
        if result.token_usage.total > context.budget.max_execution_tokens:
            raise AgentBudgetExceededError("agent execution exceeded its token budget")
        if result.tool_calls > context.budget.max_tool_calls:
            raise AgentBudgetExceededError("agent execution exceeded its tool-call budget")
        return result
