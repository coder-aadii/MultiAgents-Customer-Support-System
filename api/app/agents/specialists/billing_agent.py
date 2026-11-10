"""Billing specialist for verified invoice, refund, and ledger connector reads."""

from __future__ import annotations

from app.agents.base import AgentContext
from app.agents.tools import DynamicToolInvoker, DynamicToolValidationError, SecurityViolationError
from app.agents.tools.schemas import DynamicToolCall
from app.agents.specialists.base import SpecialistAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.connectors.registry import ConnectorRegistry, default_connector_registry
from app.config import settings
from app.gateway import EntityNotFoundError
from app.gateway.tool_gateway import ToolGateway, default_tool_gateway


class BillingSpecialistAgent(SpecialistAgent):
    agent_name = "billing_specialist"
    agent_version = "1.0"
    system_prompt = "phase_6c_billing_specialist_v1"

    _ALLOWED_OPERATIONS = {"get_refund", "get_invoice", "list_refunds", "list_invoices", "ledger"}

    def __init__(
        self,
        connector_registry: ConnectorRegistry | None = None,
        tool_gateway: ToolGateway | None = None,
        tool_invoker: DynamicToolInvoker | None = None,
    ) -> None:
        self._connector_registry = connector_registry or default_connector_registry
        self._tool_gateway = tool_gateway or default_tool_gateway
        self._tool_invoker = tool_invoker or DynamicToolInvoker(
            tool_gateway=self._tool_gateway, connector_registry=self._connector_registry
        )

    def get_capabilities(self) -> list[str]:
        return ["verified_billing_lookup"]

    def get_tool_allowlist(self) -> set[str]:
        return {"execute_get_entity", "execute_query"}

    async def investigate(self, context: AgentContext) -> SpecialistFindings:
        if not self.has_verified_session(context):
            return self.unauthenticated(self.agent_name)
        refund_id = self.payload_string(context, "refund_id") or self.payload_string(context, "entity_id")
        if refund_id is not None:
            return await self._investigate_refund_status(context, refund_id)
        connector_name = self.payload_string(context, "connector_name")
        operation = self.payload_string(context, "operation")
        if connector_name is None or operation not in self._ALLOWED_OPERATIONS:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                failure_reason="BILLING_OPERATION_NOT_ALLOWED",
            )

        assert context.visitor_session is not None
        parameters = self.payload_dict(context, "parameters") or {}
        if settings.MCSS_DYNAMIC_TOOL_CALLING_ENABLED:
            try:
                await self._tool_invoker.invoke(
                    self,
                    context,
                    DynamicToolCall(
                        name="connector_query",
                        arguments={
                            "connector_name": connector_name,
                            "operation": operation,
                            "parameters": parameters,
                        },
                    ),
                )
            except Exception:
                return SpecialistFindings(
                    specialist=self.agent_name,
                    status=SpecialistStatus.failed,
                    failure_reason="BILLING_CONNECTOR_FAILURE",
                )
            evidence_key = f"ref_billing_{operation}"
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.completed,
                evidence_keys=(evidence_key,),
                confidence=1.0,
                recommendations=(f"Retrieved verified {operation.replace('_', ' ')} information.",),
            )
        result = await self._connector_registry.execute_and_audit(
            connector_name=connector_name,
            operation=operation,
            session=context.visitor_session,
            parameters=parameters,
            db=context.db,
        )
        if not result.success:
            status = (
                SpecialistStatus.authentication_required
                if result.error_code == "AUTHORIZATION_REQUIRED"
                else SpecialistStatus.failed
            )
            return SpecialistFindings(
                specialist=self.agent_name,
                status=status,
                failure_reason=result.error_code or "BILLING_CONNECTOR_FAILURE",
            )

        evidence_key = f"ref_billing_{operation}"
        recommendations = (f"Retrieved verified {operation.replace('_', ' ')} information.",)
        return SpecialistFindings(
            specialist=self.agent_name,
            status=SpecialistStatus.completed,
            evidence_keys=(evidence_key,),
            confidence=1.0,
            recommendations=recommendations,
        )

    async def _investigate_refund_status(
        self, context: AgentContext, refund_id: str
    ) -> SpecialistFindings:
        assert context.visitor_session is not None
        try:
            if settings.MCSS_DYNAMIC_TOOL_CALLING_ENABLED:
                result = await self._tool_invoker.invoke(
                    self,
                    context,
                    DynamicToolCall(name="get_refund_status", arguments={"entity_id": refund_id}),
                )
                data = result.data
                if not isinstance(data, dict) or not isinstance(data.get("entity_id"), str):
                    return SpecialistFindings(specialist=self.agent_name, status=SpecialistStatus.no_data)
                entity_id = data["entity_id"]
                return SpecialistFindings(
                    specialist=self.agent_name,
                    status=SpecialistStatus.completed,
                    evidence_keys=(f"ref_refund_{entity_id}",),
                    confidence=1.0,
                    recommendations=(f"Retrieved verified refund status for {data.get('title', entity_id)}.",),
                )
            record = await self._tool_gateway.execute_get_entity(
                session=context.visitor_session,
                entity_type="refund",
                entity_id=refund_id,
                db=context.db,
            )
            evidence_key = f"ref_refund_{record.entity_id}"
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.completed,
                facts=tuple(record.extract_facts(evidence_key)),
                evidence_keys=(evidence_key,),
                confidence=1.0,
                recommendations=(f"Retrieved verified refund status for {record.title}.",),
            )
        except (EntityNotFoundError, DynamicToolValidationError, SecurityViolationError):
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.completed,
                confidence=1.0,
                recommendations=(
                    "For security and privacy reasons, I can only provide refund information for orders directly associated with your verified login.",
                ),
                facts=(),
                evidence_keys=("ref_refund_privacy_fence",),
            )
