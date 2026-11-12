"""Universal entity specialist querying any tenant-configured entity type."""

from __future__ import annotations

import logging
from typing import Any

from app.agents.base import AgentContext
from app.agents.specialists.base import SpecialistAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.agents.tools import DynamicToolInvoker
from app.gateway import EntityNotFoundError
from app.gateway.tool_gateway import ToolGateway, default_tool_gateway
from app.schemas.evidence import VerifiedFact

logger = logging.getLogger("mcss.agents.universal_agent")


class UniversalEntitySpecialistAgent(SpecialistAgent):
    """Generic read-only investigator querying any tenant-configured business entity.

    Decouples business data investigation from hardcoded e-commerce entities (orders, shipments)
    so any SaaS, banking, or enterprise workload can investigate custom domain entities.
    """

    agent_name = "universal_entity_specialist"
    agent_version = "1.0"
    system_prompt = "universal_entity_specialist_v1"

    def __init__(
        self,
        tool_gateway: ToolGateway | None = None,
        tool_invoker: DynamicToolInvoker | None = None,
    ) -> None:
        self._tool_gateway = tool_gateway or default_tool_gateway
        self._tool_invoker = tool_invoker or DynamicToolInvoker(tool_gateway=self._tool_gateway)

    def get_capabilities(self) -> list[str]:
        return ["universal_entity_investigation", "tenant_schema_entity_lookup"]

    def get_tool_allowlist(self) -> set[str]:
        return {"execute_get_entity", "execute_list_entities", "get_entity", "list_entities"}

    async def investigate(self, context: AgentContext) -> SpecialistFindings:
        if not self.has_verified_session(context):
            return self.unauthenticated(self.agent_name)

        entity_type = (self.payload_string(context, "entity_type") or "order").lower()
        entity_id = self.payload_string(context, "entity_id")

        assert context.visitor_session is not None
        records = []

        try:
            if entity_id:
                record = await self._tool_gateway.execute_get_entity(
                    session=context.visitor_session,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    db=context.db,
                )
                records = [record]
            elif entity_type == "customer":
                cid = context.visitor_session.verified_customer_id
                if not cid:
                    return self.unauthenticated(self.agent_name)
                record = await self._tool_gateway.execute_get_entity(
                    session=context.visitor_session,
                    entity_type="customer",
                    entity_id=cid,
                    db=context.db,
                )
                records = [record]
            else:
                records = await self._tool_gateway.execute_list_entities(
                    session=context.visitor_session,
                    entity_type=entity_type,
                    db=context.db,
                )
        except EntityNotFoundError:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.no_data,
                recommendations=(f"No {entity_type} record found with ID '{entity_id}'.",),
            )
        except Exception as exc:
            logger.warning(
                "Universal entity lookup failed for entity_type=%s, entity_id=%s: %s",
                entity_type,
                entity_id,
                exc,
                exc_info=True,
            )
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                failure_reason=f"ENTITY_LOOKUP_ERROR: {exc}",
            )

        if not records:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.no_data,
                recommendations=(f"No matching {entity_type} records were found.",),
            )

        facts: list[VerifiedFact] = []
        evidence_keys: list[str] = []
        recommendations: list[str] = []

        for record in records:
            evidence_key = f"ref_{record.entity_type}_{record.entity_id}"
            facts.extend(record.extract_facts(evidence_key))
            evidence_keys.append(evidence_key)

            # Build readable summary from summary_fields
            summary = record.summary_fields if isinstance(record.summary_fields, dict) else {}
            status_val = summary.get("status")
            tier_val = summary.get("tier")
            name_val = summary.get("name") or record.title

            if record.entity_type == "customer":
                cname = summary.get("name") or record.entity_id
                cemail = summary.get("email") or "unspecified"
                recommendations.append(
                    f"You are verified as {cname} (ID: {record.entity_id}, Email: {cemail}) with assurance level IAL 2."
                )
            elif record.entity_type == "account":
                bal = summary.get("balance")
                curr = summary.get("currency", "USD")
                atype = summary.get("account_type", "Account")
                bal_str = f"${bal:,.2f} {curr}" if isinstance(bal, (int, float)) else str(bal)
                recommendations.append(
                    f"Bank {atype} #{record.entity_id} has an available balance of {bal_str} and is currently {status_val or 'active'}."
                )
            elif record.entity_type == "transaction":
                amt = summary.get("amount")
                merch = summary.get("merchant", "Merchant")
                recommendations.append(
                    f"Transaction #{record.entity_id} for ${amt} at {merch} is {status_val or 'posted'}."
                )
            elif record.entity_type == "wire_transfer":
                amt = summary.get("amount")
                recip = summary.get("recipient", "Recipient")
                recommendations.append(
                    f"Wire transfer #{record.entity_id} of ${amt} to {recip} is currently {status_val or 'processed'}."
                )
            elif record.entity_type == "appointment":
                doc = summary.get("doctor", "Clinician")
                dt = summary.get("datetime", "scheduled time")
                dept = summary.get("department", "Clinic")
                recommendations.append(
                    f"Appointment #{record.entity_id} with {doc} ({dept}) is scheduled for {dt} ({status_val or 'confirmed'})."
                )
            elif record.entity_type == "prescription":
                med = summary.get("medication", "Medication")
                dos = summary.get("dosage", "")
                refills = summary.get("refills_remaining", 0)
                recommendations.append(
                    f"Prescription #{record.entity_id} for {med} ({dos}) has {refills} refill(s) remaining and is {status_val or 'active'}."
                )
            elif record.entity_type == "api_quota":
                used = summary.get("used_percentage")
                limit = summary.get("monthly_limit")
                recommendations.append(
                    f"API Quota #{record.entity_id} is {status_val or 'healthy'}: {used}% utilized of monthly limit {limit}."
                )
            elif record.entity_type == "workspace":
                ret = summary.get("retention_days")
                tier_str = f" on {tier_val} tier" if tier_val else ""
                ret_str = f" with {ret}-day data retention" if ret else ""
                recommendations.append(
                    f"Workspace #{record.entity_id} ('{name_val}'){tier_str} is {status_val or 'active'}{ret_str}."
                )
            else:
                desc_parts = [f"{record.entity_type.replace('_', ' ').title()} #{record.entity_id}"]
                if name_val and name_val != record.entity_id:
                    desc_parts.append(f"('{name_val}')")
                if status_val:
                    desc_parts.append(f"is currently {status_val}")
                if tier_val:
                    desc_parts.append(f"on the {tier_val} tier")
                recommendations.append(" ".join(desc_parts) + ".")

        return SpecialistFindings(
            specialist=self.agent_name,
            status=SpecialistStatus.completed,
            facts=tuple(facts),
            evidence_keys=tuple(evidence_keys),
            recommendations=tuple(recommendations),
        )
