from __future__ import annotations

import uuid

import pytest

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.agents.planner import InvestigationPlan, PlanValidationError, PlanningOutcome, TaskNode, TaskPlanner
from app.agents.registry import AgentRegistry
from app.agents.supervisor import MAX_REPLAN_ITERATIONS, SupervisorAgent
from app.providers import FakeLLMProvider


class RegisteredSpecialist(BaseAgent):
    agent_name = "business_data_specialist"

    async def run(self, context: AgentContext) -> AgentResult:
        return AgentResult()

    def get_capabilities(self) -> list[str]:
        return ["business_data_lookup"]

    def get_tool_allowlist(self) -> set[str]:
        return set()


class LogisticsSpecialist(RegisteredSpecialist):
    agent_name = "logistics_specialist"

    def get_capabilities(self) -> list[str]:
        return ["shipment_lookup"]


class BillingSpecialist(RegisteredSpecialist):
    agent_name = "billing_specialist"

    def get_capabilities(self) -> list[str]:
        return ["billing_lookup"]


class KnowledgeSpecialist(RegisteredSpecialist):
    agent_name = "knowledge_specialist"

    def get_capabilities(self) -> list[str]:
        return ["knowledge_lookup"]


def registry(all_specialists: bool = False) -> AgentRegistry:
    value = AgentRegistry()
    value.register(RegisteredSpecialist)
    if all_specialists:
        value.register(LogisticsSpecialist)
        value.register(BillingSpecialist)
        value.register(KnowledgeSpecialist)
    return value


def context(message: str) -> AgentContext:
    return AgentContext(
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        execution_phase="planning",
        payload={"user_message": message},
    )


@pytest.mark.asyncio
async def test_supervisor_deconstructs_multi_intent() -> None:
    provider = FakeLLMProvider()
    provider.queue_response(
        {
            "user_intents": ["find order", "investigate delivery", "check refund", "read policy"],
            "tasks": [
                {
                    "task_id": "orders",
                    "assigned_agent": "business_data_specialist",
                    "task_description": "find recent order",
                    "required_inputs": {},
                    "dependencies": [],
                },
                {
                    "task_id": "tracking",
                    "assigned_agent": "logistics_specialist",
                    "task_description": "check shipment tracking",
                    "required_inputs": {},
                    "dependencies": ["orders"],
                },
                {
                    "task_id": "refund",
                    "assigned_agent": "billing_specialist",
                    "task_description": "check refund status",
                    "required_inputs": {},
                    "dependencies": ["orders"],
                },
                {
                    "task_id": "policy",
                    "assigned_agent": "knowledge_specialist",
                    "task_description": "find delivery policy",
                    "required_inputs": {},
                    "dependencies": [],
                },
            ],
        }
    )
    supervisor = SupervisorAgent(provider, registry(all_specialists=True), enabled=True)

    decision = await supervisor.analyze_and_plan(
        context("My order is delayed, my refund is missing, and I need the delivery policy.")
    )

    assert decision.outcome is PlanningOutcome.plan
    assert decision.plan is not None
    assert decision.plan.user_intents == (
        "find order",
        "investigate delivery",
        "check refund",
        "read policy",
    )
    assert {task.assigned_agent for task in decision.plan.tasks} == {
        "business_data_specialist",
        "logistics_specialist",
        "billing_specialist",
        "knowledge_specialist",
    }


def test_planner_detects_cyclic_dependencies() -> None:
    planner = TaskPlanner(registry())
    plan = InvestigationPlan(
        user_intents=("test",),
        tasks=(
            TaskNode(
                task_id="first",
                assigned_agent="business_data_specialist",
                task_description="first",
                dependencies=("second",),
            ),
            TaskNode(
                task_id="second",
                assigned_agent="business_data_specialist",
                task_description="second",
                dependencies=("first",),
            ),
        ),
    )

    with pytest.raises(PlanValidationError, match="cycle"):
        planner.topological_sort(plan)


@pytest.mark.asyncio
async def test_supervisor_bounded_replanning() -> None:
    provider = FakeLLMProvider()
    supervisor = SupervisorAgent(provider, registry(), enabled=True)
    previous_plan = InvestigationPlan(
        user_intents=("find order",),
        tasks=(
            TaskNode(
                task_id="orders",
                assigned_agent="business_data_specialist",
                task_description="find order",
            ),
        ),
    )

    decision = await supervisor.replan(
        context("Find my order and refund"), previous_plan, {"orders": "missing"}, MAX_REPLAN_ITERATIONS
    )

    assert decision.outcome is PlanningOutcome.handoff
    assert decision.reason == "replanning_budget_exhausted"
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_supervisor_simple_intent_fast_path() -> None:
    provider = FakeLLMProvider()
    supervisor = SupervisorAgent(provider, registry(), enabled=True)

    decision = await supervisor.analyze_and_plan(context("Hello!"))

    assert decision.outcome is PlanningOutcome.fast_path
    assert decision.target_agent == "knowledge_specialist"
    assert provider.call_count == 0
