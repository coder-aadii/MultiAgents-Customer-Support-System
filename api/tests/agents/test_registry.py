from __future__ import annotations

import asyncio
import uuid

import pytest

from app.agents.base import (
    AgentBudget,
    AgentBudgetExceededError,
    AgentContext,
    AgentResult,
    BaseAgent,
    TenantSecurityError,
)
from app.agents.registry import AgentRegistrationError, AgentRegistry
from app.agents.schemas import AgentTokenUsage
from app.models.agent_execution import AgentExecutionAudit


class TestAgent(BaseAgent):
    agent_name = "test_agent"
    agent_version = "1.0"

    async def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(token_usage=AgentTokenUsage(prompt=4, completion=6, total=10))

    def get_capabilities(self) -> list[str]:
        return ["test_lookup"]

    def get_tool_allowlist(self) -> set[str]:
        return {"get_test_record"}


def context(**overrides: object) -> AgentContext:
    values: dict[str, object] = {
        "tenant_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "execution_phase": "registry_test",
    }
    values.update(overrides)
    return AgentContext(**values)  # type: ignore[arg-type]


def test_agent_registry_registration_and_lookup() -> None:
    registry = AgentRegistry()
    registry.register(TestAgent)

    assert registry.get("test_agent") is TestAgent
    definition = registry.get_definition("test_agent")
    assert definition is not None
    assert definition.capabilities == ("test_lookup",)
    assert definition.tool_allowlist == frozenset({"get_test_record"})


def test_registry_rejects_capability_collision() -> None:
    class ConflictingAgent(TestAgent):
        agent_name = "conflicting_agent"

    registry = AgentRegistry()
    registry.register(TestAgent)
    with pytest.raises(AgentRegistrationError, match="already owned"):
        registry.register(ConflictingAgent)


@pytest.mark.asyncio
async def test_agent_budget_timeout_enforcement() -> None:
    class SlowAgent(TestAgent):
        async def run(self, context: AgentContext) -> AgentResult:
            await asyncio.sleep(0.03)
            return AgentResult()

    with pytest.raises(AgentBudgetExceededError, match="timeout"):
        await SlowAgent().execute(context(budget=AgentBudget(max_timeout_seconds=0.001)))


def test_agent_context_tenant_enforcement() -> None:
    with pytest.raises(TenantSecurityError):
        context(tenant_id=None)


def test_agent_execution_audit_logging_redacts_sensitive_values() -> None:
    audit = AgentExecutionAudit(
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        agent_name="test_agent",
        agent_version="1.0",
        execution_phase="registry_test",
        input_payload_json={"email": "customer@example.com", "api_key": "secret-value"},
        output_payload_json={"message": "Call +1 (415) 555-0100", "completion": "raw model text"},
        token_usage={"prompt": 4, "completion": 6, "total": 10},
        execution_duration_ms=12.5,
        status="success",
    )

    assert audit.input_payload_json["email"] == "[REDACTED_EMAIL]"
    assert audit.input_payload_json["api_key"] == "[REDACTED]"
    assert audit.output_payload_json["message"] == "Call [REDACTED_PHONE]"
    assert audit.output_payload_json["completion"] == "[REDACTED]"
