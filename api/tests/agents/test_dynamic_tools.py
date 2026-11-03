from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.agents.base import AgentContext
from app.agents.specialists.business_data_agent import BusinessDataSpecialistAgent
from app.agents.specialists.billing_agent import BillingSpecialistAgent
from app.agents.specialists.knowledge_agent import KnowledgeSpecialistAgent
from app.agents.tools import (
    DynamicToolCall,
    DynamicToolInvoker,
    DynamicToolValidationError,
)
from app.gateway.base import EntityRecord, ToolAuthorizationError, ToolTimeoutError
from app.agents.tools.sanitizer import SecurityViolationError
from app.tenancy.context import TenantContext


class RecordingGateway:
    def __init__(self) -> None:
        self.parameters: dict | None = None
        self.customer_id: str | None = None
        self.tenant_id: uuid.UUID | None = None

    async def execute_tool(self, session, capability_name, parameters, **_kwargs):
        self.parameters = parameters
        self.customer_id = session.verified_customer_id
        self.tenant_id = TenantContext.get_tenant_id()
        return EntityRecord(
            entity_type="order",
            entity_id="ORD-88219",
            customer_id=session.verified_customer_id,
            title="Order ORD-88219",
            summary_fields={"status": "processing"},
        )


class SlowGateway(RecordingGateway):
    async def execute_tool(self, *args, **kwargs):
        await asyncio.sleep(5.1)
        return await super().execute_tool(*args, **kwargs)


class ReadOnlyConnectorRegistry:
    class Connector:
        supported_operations = {"get_refund"}

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_connector(self, _name):
        return self.Connector()

    async def execute_and_audit(self, connector_name, operation, **_kwargs):
        self.calls.append(f"{connector_name}:{operation}")
        return SimpleNamespace(success=True, data={"status": "pending"}, error_code=None)


def context() -> AgentContext:
    customer_id = "cust_verified"
    return AgentContext(
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        execution_phase="dynamic_tool_test",
        verified_customer_id=customer_id,
        visitor_session=SimpleNamespace(verified_customer_id=customer_id),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_dynamic_tool_calling_strips_spoofed_customer_id() -> None:
    gateway = RecordingGateway()
    invocation_context = context()
    invoker = DynamicToolInvoker(tool_gateway=gateway, enabled=True)  # type: ignore[arg-type]

    result = await invoker.invoke(
        BusinessDataSpecialistAgent(),
        invocation_context,
        DynamicToolCall(
            name="get_order_summary",
            arguments={
                "entity_id": "ORD-88219",
                "customer_id": "cust_attacker",
                "tenant_id": str(uuid.uuid4()),
            },
        ),
    )

    assert gateway.parameters == {"entity_id": "ORD-88219"}
    assert gateway.customer_id == "cust_verified"
    assert gateway.tenant_id == invocation_context.tenant_id
    assert result.data is not None


@pytest.mark.asyncio
async def test_dynamic_tool_calling_rejects_unallowed_tool() -> None:
    invoker = DynamicToolInvoker(tool_gateway=RecordingGateway(), enabled=True)  # type: ignore[arg-type]

    with pytest.raises(ToolAuthorizationError):
        await invoker.invoke(
            KnowledgeSpecialistAgent(),
            context(),
            DynamicToolCall(name="get_order_summary", arguments={"entity_id": "ORD-88219"}),
        )


@pytest.mark.asyncio
async def test_dynamic_tool_input_validation_regex() -> None:
    invoker = DynamicToolInvoker(tool_gateway=RecordingGateway(), enabled=True)  # type: ignore[arg-type]

    with pytest.raises(DynamicToolValidationError, match="invalid 'entity_id' format"):
        await invoker.invoke(
            BusinessDataSpecialistAgent(),
            context(),
            DynamicToolCall(name="get_order_summary", arguments={"entity_id": "DROP TABLE orders"}),
        )


@pytest.mark.asyncio
async def test_dynamic_tool_timeout() -> None:
    invoker = DynamicToolInvoker(tool_gateway=SlowGateway(), enabled=True)  # type: ignore[arg-type]

    with pytest.raises(ToolTimeoutError):
        await invoker.invoke(
            BusinessDataSpecialistAgent(),
            context(),
            DynamicToolCall(name="get_order_summary", arguments={"entity_id": "ORD-88219"}),
        )


@pytest.mark.asyncio
async def test_dynamic_connector_allows_refund_read_and_rejects_mutation() -> None:
    registry = ReadOnlyConnectorRegistry()
    invoker = DynamicToolInvoker(connector_registry=registry, enabled=True)  # type: ignore[arg-type]
    agent = BillingSpecialistAgent()

    await invoker.invoke(
        agent,
        context(),
        DynamicToolCall(
            name="connector_query",
            arguments={"connector_name": "billing", "operation": "get_refund", "parameters": {}},
        ),
    )
    assert registry.calls == ["billing:get_refund"]

    with pytest.raises(SecurityViolationError):
        await invoker.invoke(
            agent,
            context(),
            DynamicToolCall(
                name="connector_query",
                arguments={"connector_name": "billing", "operation": "create_refund", "parameters": {}},
            ),
        )
