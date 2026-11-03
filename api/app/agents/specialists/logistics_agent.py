"""Logistics specialist for verified shipment tracking and carrier events."""

from __future__ import annotations

from app.agents.base import AgentContext
from app.agents.tools import DynamicToolInvoker
from app.agents.tools.schemas import DynamicToolCall
from app.agents.specialists.base import SpecialistAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.config import settings
from app.gateway.tool_gateway import ToolGateway, default_tool_gateway
from app.schemas.evidence import VerifiedFact


class LogisticsSpecialistAgent(SpecialistAgent):
    agent_name = "logistics_specialist"
    agent_version = "1.0"
    system_prompt = "phase_6c_logistics_specialist_v1"

    def __init__(self, tool_gateway: ToolGateway | None = None, tool_invoker: DynamicToolInvoker | None = None) -> None:
        self._tool_gateway = tool_gateway or default_tool_gateway
        self._tool_invoker = tool_invoker or DynamicToolInvoker(tool_gateway=self._tool_gateway)

    def get_capabilities(self) -> list[str]:
        return ["verified_shipment_tracking"]

    def get_tool_allowlist(self) -> set[str]:
        return {"execute_get_entity"}

    async def investigate(self, context: AgentContext) -> SpecialistFindings:
        if not self.has_verified_session(context):
            return self.unauthenticated(self.agent_name)
        shipment_id = self.payload_string(context, "shipment_id") or self.payload_string(context, "entity_id")
        if shipment_id is None:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                failure_reason="SHIPMENT_ID_REQUIRED",
            )
        assert context.visitor_session is not None
        if settings.MCSS_DYNAMIC_TOOL_CALLING_ENABLED:
            result = await self._tool_invoker.invoke(
                self, context, DynamicToolCall(name="get_shipment_status", arguments={"entity_id": shipment_id})
            )
            if not isinstance(result.data, dict) or not isinstance(result.data.get("entity_id"), str):
                return SpecialistFindings(specialist=self.agent_name, status=SpecialistStatus.no_data)
            entity_id = result.data["entity_id"]
            evidence_key = f"ref_shipment_{entity_id}"
            summary = result.data.get("summary_fields", {})
            summary_fields = summary if isinstance(summary, dict) else {}
            facts = tuple(
                VerifiedFact(entity_type="shipment", entity_id=entity_id, field_name=str(key),
                             field_value=value, source_evidence_id=evidence_key)
                for key, value in summary_fields.items() if isinstance(key, str)
            )
            return SpecialistFindings(
                specialist=self.agent_name, status=SpecialistStatus.completed, facts=facts,
                evidence_keys=(evidence_key,), confidence=1.0,
                recommendations=(f"Retrieved current carrier details for {result.data.get('title', entity_id)}.",),
            )
        record = await self._tool_gateway.execute_get_entity(
            session=context.visitor_session,
            entity_type="shipment",
            entity_id=shipment_id,
            db=context.db,
        )
        evidence_key = f"ref_shipment_{record.entity_id}"
        return SpecialistFindings(
            specialist=self.agent_name,
            status=SpecialistStatus.completed,
            facts=tuple(record.extract_facts(evidence_key)),
            evidence_keys=(evidence_key,),
            confidence=1.0,
            recommendations=(f"Retrieved current carrier details for {record.title}.",),
        )
