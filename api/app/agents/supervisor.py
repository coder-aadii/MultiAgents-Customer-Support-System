"""Phase 6B supervisor that generates validated plans but never executes them."""

from __future__ import annotations

import json
import re
from typing import Any

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.agents.planner import InvestigationPlan, PlanningDecision, PlanningOutcome, TaskPlanner
from app.agents.registry import AgentRegistry, default_agent_registry
from app.agents.schemas import AgentExecutionStatus, AgentTokenUsage, AgentTraceEvent
from app.config import settings
from app.prompts.supervisor_planner import build_supervisor_planner_prompt
from app.providers import LLMMessage, LLMProvider, LLMRequest, get_llm_provider

MAX_USER_MESSAGE_CHARS = 2_000
MAX_REPLAN_ITERATIONS = 2
_GREETING = re.compile(
    r"^(?:h+[iey]+l*o*|hew+l+o+|hey+|hi+|howdy|sup|good (?:morning|afternoon|evening|day)|greetings|thanks?|thank you)[!?.\s]*$",
    re.I,
)
_IDENTITY_PATTERN = re.compile(
    r"\b("
    r"who\s*am\s*i|whoami|"
    r"my\s+(?:e-?mail|eamil|account|profile|info|information|details|name|username|id|login)|"
    r"what(?:'s|\s+is)\s+my\s+(?:e-?mail|eamil|account|profile|info|information|details|name|username|id|login)|"
    r"(?:tell|show)\s+(?:me\s+)?my\s+(?:e-?mail|eamil|account|profile|info|details|name)|"
    r"logged\s+in\s+as|customer\s*id"
    r")\b",
    re.IGNORECASE,
)


def is_identity_query(text: str) -> bool:
    if not text:
        return False
    if re.search(r"\b(?:change|update|edit|modify|reset|cancel|delete|remove)\b", text, re.I):
        return False
    return bool(_IDENTITY_PATTERN.search(text))


# Intent conjunctions mark a multi-intent message that must run through the slow
# multi-intent planner rather than collapsing to a single fast-path specialist.
_MULTI_INTENT_PATTERN = re.compile(r"\b(?:and|or|but|also|plus)\b", re.IGNORECASE)


def is_multi_intent_query(text: str) -> bool:
    if not text:
        return False
    return bool(_MULTI_INTENT_PATTERN.search(text))


class SupervisorAgent(BaseAgent):
    """Converts multi-intent messages to safe DAGs under strict planning limits."""

    agent_name = "supervisor"
    agent_version = "1.0"
    system_prompt = "phase_6b_supervisor_planner_v1"

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        registry: AgentRegistry | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._provider = llm_provider
        self._registry = registry or default_agent_registry
        self._planner = TaskPlanner(self._registry)
        self._enabled = (
            settings.MCSS_MULTI_AGENT_ORCHESTRATION_ENABLED if enabled is None else enabled
        )

    def get_capabilities(self) -> list[str]:
        return ["multi_intent_planning"]

    def get_tool_allowlist(self) -> set[str]:
        return set()

    async def run(self, context: AgentContext) -> AgentResult:
        decision, token_usage = await self._analyze(context)
        return AgentResult(
            status=AgentExecutionStatus.success,
            payload=decision.model_dump(mode="json"),
            token_usage=token_usage,
            trace=(
                AgentTraceEvent(
                    event="planning_decision",
                    details={"outcome": decision.outcome, "reason": decision.reason},
                ),
            ),
        )

    async def analyze_and_plan(self, context: AgentContext) -> PlanningDecision:
        """Return a direct route or validated DAG. No task is run in this phase."""
        decision, _ = await self._analyze(context)
        return decision

    async def replan(
        self,
        context: AgentContext,
        previous_plan: InvestigationPlan,
        missing_or_conflicting_findings: dict[str, Any],
        attempts_completed: int,
    ) -> PlanningDecision:
        """Create at most two replacement plans; subsequent failures are handed to staff."""
        if attempts_completed >= MAX_REPLAN_ITERATIONS:
            return PlanningDecision.handoff("replanning_budget_exhausted", attempts_completed)
        if attempts_completed < 0:
            return PlanningDecision.handoff("invalid_replanning_attempt")

        planning_context = AgentContext(
            tenant_id=context.tenant_id,
            conversation_id=context.conversation_id,
            execution_phase="replan",
            verified_customer_id=context.verified_customer_id,
            budget=context.budget,
            db=context.db,
            visitor_session=context.visitor_session,
            payload={
                **context.payload,
                "previous_plan": previous_plan.model_dump(mode="json"),
                "missing_or_conflicting_findings": missing_or_conflicting_findings,
            },
        )
        decision, _ = await self._analyze(planning_context, attempts_completed + 1)
        return decision

    async def _analyze(
        self, context: AgentContext, replan_attempt: int = 0
    ) -> tuple[PlanningDecision, AgentTokenUsage]:
        if not self._enabled:
            return PlanningDecision.handoff("multi_agent_orchestration_disabled", replan_attempt), AgentTokenUsage()

        entity_schema_raw = context.payload.get("entity_schema")
        entity_schema = None
        if entity_schema_raw:
            from app.schemas.entities import OrganizationEntitySchema
            if isinstance(entity_schema_raw, OrganizationEntitySchema):
                entity_schema = entity_schema_raw
            elif isinstance(entity_schema_raw, dict):
                try:
                    entity_schema = OrganizationEntitySchema.model_validate(entity_schema_raw)
                except Exception:
                    pass

        if entity_schema is None and context.db is not None and context.tenant_id is not None:
            from app.models.organization import Organization
            from app.schemas.entities import OrganizationEntitySchema
            org = await context.db.get(Organization, context.tenant_id)
            if org and org.entity_schemas_json and org.entity_schemas_json.get("entities"):
                try:
                    entity_schema = OrganizationEntitySchema.model_validate(org.entity_schemas_json)
                except Exception:
                    pass
        if entity_schema is None:
            from app.schemas.entities import get_default_olist_entity_schema
            entity_schema = get_default_olist_entity_schema()

        user_message = self._user_message(context)
        fast_path_target = self._fast_path_target(user_message, entity_schema=entity_schema)
        if fast_path_target:
            return (
                PlanningDecision(
                    outcome=PlanningOutcome.fast_path,
                    reason="single_intent_fast_path",
                    target_agent=fast_path_target,
                    replan_attempt=replan_attempt,
                ),
                AgentTokenUsage(),
            )

        if not self._registry.list_agents():
            return PlanningDecision.handoff("no_registered_specialists", replan_attempt), AgentTokenUsage()

        request = LLMRequest(
            messages=[
                LLMMessage(
                    role="system",
                    content=build_supervisor_planner_prompt(self._registry.list_agents(), entity_schema=entity_schema),
                ),
                LLMMessage(role="user", content=user_message),
            ],
            temperature=0.0,
            max_tokens=min(context.budget.max_execution_tokens, 1024),
            response_format={"type": "json_object"},
            timeout=min(context.budget.max_timeout_seconds, 8.0),
        )
        try:
            response = await (self._provider or get_llm_provider()).chat_complete(request)
            raw_plan = json.loads(response.content)
            plan = self._planner.validate(InvestigationPlan.model_validate(raw_plan))
            token_usage = AgentTokenUsage(
                prompt=response.prompt_tokens,
                completion=response.completion_tokens,
                total=response.total_tokens,
            )
            return (
                PlanningDecision(
                    outcome=PlanningOutcome.plan,
                    reason="validated_multi_intent_plan",
                    plan=plan,
                    replan_attempt=replan_attempt,
                ),
                token_usage,
            )
        except Exception:
            return PlanningDecision.handoff("planner_provider_or_validation_failure", replan_attempt), AgentTokenUsage()

    @staticmethod
    def _user_message(context: AgentContext) -> str:
        message = context.payload.get("user_message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("SupervisorAgent requires a non-empty payload.user_message")
        return message.strip()[:MAX_USER_MESSAGE_CHARS]

    @staticmethod
    def _fast_path_target(message: str, entity_schema: Any = None) -> str | None:
        if _GREETING.fullmatch(message):
            return "knowledge_specialist"

        normalized = message.lower()

        # Check for customer account / identity / profile inquiries
        if is_identity_query(normalized):
            return "business_data_specialist"

        # A conjunction between two intents is a multi-intent message: let the slow
        # planner build a DAG instead of collapsing to one fast-path specialist.
        if is_multi_intent_query(normalized):
            return None

        # Check for how-to / process inquiries. These are answered from the
        # knowledge base, never from a customer's live records, so they take priority
        # over personal record lookups when both match (e.g. "how can I check when my
        # order was approved" or "how to cancel my order" is process guidance, not a direct data request).
        is_process_how_to = any(
            phrase in normalized
            for phrase in (
                "how can i",
                "how do i",
                "how to",
                "how does",
                "how am",
                "procedure",
            )
        )

        # Check for customer-specific record lookups (asking for own records or a
        # specific entity). Generic phrasing ("status of order", "when was order") is
        # intentionally excluded: those are definition questions, not data requests.
        is_record_lookup = any(
            phrase in normalized
            for phrase in (
                "show my",
                "list my",
                "when was my",
                "status of my",
                "what is my",
                "what was my",
                "what are my",
                "my order",
                "my recent",
                "recent orders",
                "last order",
                "is my order",
                "where is my",
                "track my",
            )
        ) or bool(re.search(r"\b(?:ord-|order\s+[0-9a-f]{6,})\b", normalized))

        # Check for policy, status definition, and FAQ questions.
        is_policy_inquiry = any(
            phrase in normalized
            for phrase in (
                "policy",
                "return",
                "warranty",
                "hours",
                "faq",
                "meaning of",
                "explain",
                "guidance",
                "what does",
                "what are",
                "what can",
                "can i cancel",
                "what status",
                "what statuses",
                "order status",
                "status mean",
                "means",
                "definition",
                "definitions",
                "cancel order policy",
                "cancellation policy",
            )
        ) or (
            any(phrase in normalized for phrase in ("what is", "tell me about"))
            and not is_record_lookup
        )

        domains = set()

        # Personal record lookups route to the business-data specialist only when the
        # message is a direct order/account data request (not a shipment tracking or billing request).
        # A process how-to framing or policy inquiry always wins (e.g. "how to cancel my order" or
        # "what is my order status" guides policy/definitions rather than performing a raw data dump).
        is_logistics = any(term in normalized for term in ("tracking", "shipment", "carrier", "delivery", "delayed"))
        is_billing = any(term in normalized for term in ("refund", "invoice", "billing", "payment", "charge"))

        matched_custom_entity = None
        if entity_schema and getattr(entity_schema, "entities", None):
            for ent in entity_schema.entities:
                if ent.name not in ("order", "shipment", "refund", "customer"):
                    if ent.identifier_pattern:
                        try:
                            if re.search(ent.identifier_pattern, message, re.I):
                                matched_custom_entity = ent.name
                                break
                        except re.error:
                            pass
                    if any(
                        phrase in normalized
                        for phrase in (
                            f"my {ent.name}",
                            f"{ent.name} status",
                            f"status of {ent.name}",
                            f"status of my {ent.name}",
                            f"where is my {ent.name}",
                            f"check {ent.name}",
                            f"check my {ent.name}",
                            f"list my {ent.name}s",
                            f"show my {ent.name}s",
                            f"list {ent.name}s",
                        )
                    ):
                        matched_custom_entity = ent.name
                        break

        if (
            is_record_lookup
            and not is_process_how_to
            and not is_policy_inquiry
            and not (is_logistics or is_billing)
            and not matched_custom_entity
        ):
            domains.add("business_data_specialist")

        if matched_custom_entity and not is_process_how_to and not is_policy_inquiry:
            domains.add("universal_entity_specialist")

        if is_logistics:
            domains.add("logistics_specialist")

        if is_billing:
            domains.add("billing_specialist")

        if is_process_how_to or is_policy_inquiry:
            domains.add("knowledge_specialist")

        return next(iter(domains)) if len(domains) == 1 else None
