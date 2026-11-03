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
_GREETING = re.compile(r"^(?:hi|hello|hey|good (?:morning|afternoon|evening)|thanks?|thank you)[!?.\s]*$", re.I)


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

        user_message = self._user_message(context)
        fast_path_target = self._fast_path_target(user_message)
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
                    content=build_supervisor_planner_prompt(self._registry.list_agents()),
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
    def _fast_path_target(message: str) -> str | None:
        if _GREETING.fullmatch(message):
            return "knowledge_specialist"

        normalized = message.lower()
        domains = set()
        if any(term in normalized for term in ("order", "customer", "purchase")):
            domains.add("business_data_specialist")
        if any(term in normalized for term in ("tracking", "shipment", "carrier", "delivery", "delayed")):
            domains.add("logistics_specialist")
        if any(term in normalized for term in ("refund", "invoice", "billing", "payment", "charge")):
            domains.add("billing_specialist")
        if any(term in normalized for term in ("policy", "return", "warranty", "hours", "faq")):
            domains.add("knowledge_specialist")

        return next(iter(domains)) if len(domains) == 1 else None
