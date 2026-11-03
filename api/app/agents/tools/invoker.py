"""Runtime dispatcher for bounded, read-only, zero-trust dynamic tool calls."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from app.agents.base import AgentContext, BaseAgent
from app.agents.tools.sanitizer import (
    DynamicToolValidationError,
    SecurityViolationError,
    sanitize_tool_call,
)
from app.agents.tools.schemas import DynamicToolCall, DynamicToolResult, as_openai_tools
from app.common.redaction import sanitize_audit_payload
from app.config import settings
from app.connectors.registry import ConnectorRegistry, default_connector_registry
from app.gateway.base import EntityRecord, ToolAuthorizationError, ToolTimeoutError
from app.gateway.tool_gateway import ToolGateway, default_tool_gateway
from app.tenancy.context import TenantContext


@dataclass(frozen=True, slots=True)
class _ToolAccessPolicy:
    allowed_agent_names: frozenset[str]
    required_agent_permission: str


_TOOL_ACCESS_POLICIES = {
    "get_order_summary": _ToolAccessPolicy(
        frozenset({"business_data_specialist"}), "execute_get_entity"
    ),
    "list_customer_orders": _ToolAccessPolicy(
        frozenset({"business_data_specialist"}), "execute_list_entities"
    ),
    "get_shipment_status": _ToolAccessPolicy(
        frozenset({"logistics_specialist"}), "execute_get_entity"
    ),
    "get_refund_status": _ToolAccessPolicy(
        frozenset({"billing_specialist"}), "execute_get_entity"
    ),
    "connector_query": _ToolAccessPolicy(
        frozenset({"billing_specialist"}), "execute_query"
    ),
}
# Connector operation names are server configuration, not model instructions.
# Do not use substring heuristics here: `get_refund` is a read whereas
# `create_refund` is a mutation.  New dynamic connector reads require an
# explicit security review and allowlist addition.
_APPROVED_READ_ONLY_CONNECTOR_OPERATIONS = frozenset(
    {"get_refund", "get_invoice", "list_refunds", "list_invoices", "ledger"}
)


class DynamicToolInvoker:
    """Executes only fixed, read-only capabilities after server-side reauthorization."""

    MAX_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        tool_gateway: ToolGateway | None = None,
        connector_registry: ConnectorRegistry | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._tool_gateway = tool_gateway or default_tool_gateway
        self._connector_registry = connector_registry or default_connector_registry
        self._enabled = settings.MCSS_DYNAMIC_TOOL_CALLING_ENABLED if enabled is None else enabled

    def available_tools(self, agent: BaseAgent) -> list[dict[str, Any]]:
        """Return OpenAI tool definitions permitted to this exact specialist identity."""
        agent_permissions = agent.get_tool_allowlist()
        names = {
            name
            for name, policy in _TOOL_ACCESS_POLICIES.items()
            if agent.agent_name in policy.allowed_agent_names
            and policy.required_agent_permission in agent_permissions
        }
        return as_openai_tools(names)

    async def invoke(
        self,
        agent: BaseAgent,
        context: AgentContext,
        tool_call: DynamicToolCall,
    ) -> DynamicToolResult:
        """Authorize, sanitize, bind identity, and dispatch a single read-only call."""
        if not self._enabled:
            raise SecurityViolationError("dynamic tool calling is disabled")
        self._assert_tool_authorized(agent, tool_call.name)
        verified_customer_id = self._verified_customer_id(context)
        sanitized = sanitize_tool_call(
            name=tool_call.name,
            arguments=tool_call.arguments,
            tenant_id=context.tenant_id,
            verified_customer_id=verified_customer_id,
        )

        tenant_tokens = TenantContext.set_tenant(
            sanitized.tenant_id, TenantContext.get_tenant_slug()
        )
        started = time.monotonic()
        try:
            data = await asyncio.wait_for(
                self._dispatch(context, sanitized.name, sanitized.parameters),
                timeout=self.MAX_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise ToolTimeoutError(
                f"Dynamic tool '{sanitized.name}' timed out after {self.MAX_TIMEOUT_SECONDS}s"
            ) from exc
        finally:
            TenantContext.reset(tenant_tokens)

        return DynamicToolResult(
            name=sanitized.name,
            data=self._safe_result_data(data),
            execution_time_ms=int((time.monotonic() - started) * 1000),
        )

    def _assert_tool_authorized(self, agent: BaseAgent, tool_name: str) -> None:
        policy = _TOOL_ACCESS_POLICIES.get(tool_name)
        if policy is None:
            raise DynamicToolValidationError(f"dynamic tool '{tool_name}' is not registered")
        if (
            agent.agent_name not in policy.allowed_agent_names
            or policy.required_agent_permission not in agent.get_tool_allowlist()
        ):
            raise ToolAuthorizationError(
                f"agent '{agent.agent_name}' is not allowed to invoke '{tool_name}'"
            )

    @staticmethod
    def _verified_customer_id(context: AgentContext) -> str:
        session = context.visitor_session
        session_customer_id = getattr(session, "verified_customer_id", None) if session else None
        if not session_customer_id or not isinstance(session_customer_id, str):
            raise ToolAuthorizationError("a verified customer session is required")
        if (
            context.verified_customer_id is not None
            and context.verified_customer_id != session_customer_id
        ):
            raise SecurityViolationError("agent context customer identity does not match verified session")
        return session_customer_id

    async def _dispatch(
        self, context: AgentContext, tool_name: str, parameters: dict[str, Any]
    ) -> EntityRecord | dict[str, Any] | list[dict[str, Any]] | None:
        assert context.visitor_session is not None
        if tool_name == "connector_query":
            return await self._execute_connector_query(context, parameters)
        return await self._tool_gateway.execute_tool(
            session=context.visitor_session,
            capability_name=tool_name,
            parameters=parameters,
            db=context.db,
            timeout_seconds=self.MAX_TIMEOUT_SECONDS,
        )

    async def _execute_connector_query(
        self, context: AgentContext, parameters: dict[str, Any]
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        connector_name = parameters["connector_name"]
        operation = parameters["operation"]
        if operation not in _APPROVED_READ_ONLY_CONNECTOR_OPERATIONS:
            raise SecurityViolationError("connector operation is not approved for dynamic read access")
        connector = self._connector_registry.get_connector(connector_name)
        if connector is None or operation not in connector.supported_operations:
            raise DynamicToolValidationError("connector operation is not registered for dynamic invocation")

        assert context.visitor_session is not None
        result = await self._connector_registry.execute_and_audit(
            connector_name=connector_name,
            operation=operation,
            session=context.visitor_session,
            parameters=parameters.get("parameters", {}),
            timeout_seconds=self.MAX_TIMEOUT_SECONDS,
            db=context.db,
        )
        if not result.success:
            raise DynamicToolValidationError(result.error_code or "connector query failed")
        return result.data

    @staticmethod
    def _safe_result_data(
        result: EntityRecord | dict[str, Any] | list[dict[str, Any]] | None,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        if isinstance(result, EntityRecord):
            payload: dict[str, Any] = {
                "entity_type": result.entity_type,
                "entity_id": result.entity_id,
                "title": result.title,
                "summary_fields": result.summary_fields,
            }
        elif result is None:
            return None
        else:
            payload = {"data": result}
        sanitized = sanitize_audit_payload(payload)
        data = sanitized.get("data", sanitized)
        if isinstance(data, dict | list):
            return data
        return None
