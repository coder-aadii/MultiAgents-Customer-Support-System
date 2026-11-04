"""Business data specialist for verified customer and order lookups."""

from __future__ import annotations

from app.agents.base import AgentContext
from app.agents.tools import DynamicToolInvoker
from app.agents.tools.schemas import DynamicToolCall
from app.agents.specialists.base import SpecialistAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.config import settings
from app.gateway.tool_gateway import ToolGateway, default_tool_gateway
from app.schemas.evidence import VerifiedFact


class BusinessDataSpecialistAgent(SpecialistAgent):
    agent_name = "business_data_specialist"
    agent_version = "1.0"
    system_prompt = "phase_6c_business_data_specialist_v1"

    def __init__(self, tool_gateway: ToolGateway | None = None, tool_invoker: DynamicToolInvoker | None = None) -> None:
        self._tool_gateway = tool_gateway or default_tool_gateway
        self._tool_invoker = tool_invoker or DynamicToolInvoker(tool_gateway=self._tool_gateway)

    def get_capabilities(self) -> list[str]:
        return ["verified_business_data_lookup"]

    def get_tool_allowlist(self) -> set[str]:
        return {"execute_get_entity", "execute_list_entities"}

    async def investigate(self, context: AgentContext) -> SpecialistFindings:
        if not self.has_verified_session(context):
            return self.unauthenticated(self.agent_name)
        entity_type = (self.payload_string(context, "entity_type") or "order").lower()
        if entity_type not in {"order", "customer"}:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                failure_reason="ENTITY_TYPE_NOT_ALLOWED",
            )

        assert context.visitor_session is not None
        entity_id = self.payload_string(context, "entity_id")
        if settings.MCSS_DYNAMIC_TOOL_CALLING_ENABLED and entity_id and entity_type == "order":
            result = await self._tool_invoker.invoke(
                self, context, DynamicToolCall(name="get_order_summary", arguments={"entity_id": entity_id})
            )
            return self._dynamic_findings(result.data)
        if entity_id:
            records = [
                await self._tool_gateway.execute_get_entity(
                    session=context.visitor_session,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    db=context.db,
                )
            ]
        elif entity_type == "order":
            records = await self._tool_gateway.execute_list_entities(
                session=context.visitor_session,
                entity_type="order",
                db=context.db,
            )
        else:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                failure_reason="CUSTOMER_ENTITY_ID_REQUIRED",
            )

        if not records:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.no_data,
                recommendations=("No matching customer records were found.",),
            )
        facts = []
        evidence_keys = []
        recommendations = []
        for record in records:
            evidence_key = f"ref_{record.entity_type}_{record.entity_id}"
            facts.extend(record.extract_facts(evidence_key))
            evidence_keys.append(evidence_key)
            status = record.summary_fields.get("status") if isinstance(record.summary_fields, dict) else None
            if status:
                recommendations.append(f"Order #{record.entity_id[:8]} is currently {status}.")
            else:
                recommendations.append(f"Retrieved {record.title}.")
        return SpecialistFindings(
            specialist=self.agent_name,
            status=SpecialistStatus.completed,
            facts=tuple(facts),
            evidence_keys=tuple(evidence_keys),
            confidence=1.0,
            recommendations=tuple(recommendations),
        )

    def _dynamic_findings(self, data: object) -> SpecialistFindings:
        if not isinstance(data, dict) or not isinstance(data.get("entity_id"), str):
            return SpecialistFindings(specialist=self.agent_name, status=SpecialistStatus.no_data,
                                      recommendations=("No matching customer records were found.",))
        entity_id = data["entity_id"]
        entity_type = str(data.get("entity_type", "order"))
        evidence_key = f"ref_{entity_type}_{entity_id}"
        summary = data.get("summary_fields", {})
        status = summary.get("status") if isinstance(summary, dict) else None
        title = f"Order #{entity_id[:8]} is currently {status}." if status else f"Retrieved {str(data.get('title', entity_id))}."
        facts = tuple(
            VerifiedFact(entity_type=entity_type, entity_id=entity_id, field_name=str(key),
                         field_value=value, source_evidence_id=evidence_key)
            for key, value in summary.items() if isinstance(key, str)
        ) if isinstance(summary, dict) else ()
        return SpecialistFindings(
            specialist=self.agent_name, status=SpecialistStatus.completed, facts=facts,
            evidence_keys=(evidence_key,), confidence=1.0,
            recommendations=(title,),
        )
