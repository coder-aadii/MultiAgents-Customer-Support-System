from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.agents.orchestration.blackboard import DurableBlackboard
from app.agents.orchestration.executor import (
    MultiAgentWorkflowExecutor,
    WorkflowExecutionStatus,
)
from app.agents.planner import InvestigationPlan, TaskNode
from app.agents.registry import AgentRegistry


class FastAgent(BaseAgent):
    agent_name = "fast_agent"

    async def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(payload={"task_id": context.payload["task_id"]})

    def get_capabilities(self) -> list[str]:
        return ["fast_lookup"]

    def get_tool_allowlist(self) -> set[str]:
        return set()


class SlowAgent(FastAgent):
    agent_name = "slow_agent"

    async def run(self, context: AgentContext) -> AgentResult:
        await asyncio.sleep(0.05)
        return await super().run(context)

    def get_capabilities(self) -> list[str]:
        return ["slow_lookup"]


class MemoryExecutor(MultiAgentWorkflowExecutor):
    def __init__(self, blackboard: DurableBlackboard, registry: AgentRegistry) -> None:
        super().__init__(db_factory=lambda: None, registry=registry, enabled=True)
        self.blackboard = blackboard
        self.checkpoint_id = uuid.uuid4()
        self.preempt = False
        self.persisted_snapshots: list[set[str]] = []
        self.terminal_status = None

    async def _load_or_create(self, job_id, context, plan):
        return self.blackboard, self.checkpoint_id

    async def _persist_progress(self, checkpoint_id, blackboard):
        self.persisted_snapshots.append(set(blackboard.completed_tasks))
        return not self.preempt

    async def _preempt_if_staff_taken_over(self, job_id, checkpoint_id, context):
        return self.preempt

    async def _set_terminal_status(self, job_id, checkpoint_id, status):
        self.terminal_status = status


def registry() -> AgentRegistry:
    value = AgentRegistry()
    value.register(FastAgent)
    value.register(SlowAgent)
    return value


def context() -> AgentContext:
    return AgentContext(
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        execution_phase="workflow_test",
        payload={"user_message": "test workflow"},
    )


def plan(*tasks: TaskNode) -> InvestigationPlan:
    return InvestigationPlan(user_intents=("test",), tasks=tasks)


@pytest.mark.asyncio
async def test_workflow_parallel_execution() -> None:
    workflow_plan = plan(
        TaskNode(task_id="first", assigned_agent="slow_agent", task_description="first"),
        TaskNode(task_id="second", assigned_agent="slow_agent", task_description="second"),
    )
    invocation_context = context()
    executor = MemoryExecutor(
        DurableBlackboard(
            tenant_id=invocation_context.tenant_id,
            conversation_id=invocation_context.conversation_id,
            plan=workflow_plan,
            initial_message="test",
        ),
        registry(),
    )

    started = time.monotonic()
    result = await executor.execute(uuid.uuid4(), invocation_context, workflow_plan)

    assert result.status is WorkflowExecutionStatus.completed
    assert time.monotonic() - started < 0.09
    assert {"first", "second"} in executor.persisted_snapshots


@pytest.mark.asyncio
async def test_workflow_checkpoint_recovery() -> None:
    workflow_plan = plan(
        TaskNode(task_id="first", assigned_agent="fast_agent", task_description="first"),
        TaskNode(
            task_id="second",
            assigned_agent="fast_agent",
            task_description="second",
            dependencies=("first",),
        ),
    )
    invocation_context = context()
    blackboard = DurableBlackboard(
        tenant_id=invocation_context.tenant_id,
        conversation_id=invocation_context.conversation_id,
        plan=workflow_plan,
        initial_message="test",
        completed_tasks={"first"},
        task_results={"first": {"status": "success"}},
    )
    executor = MemoryExecutor(blackboard, registry())

    result = await executor.execute(uuid.uuid4(), invocation_context)

    assert result.status is WorkflowExecutionStatus.completed
    assert result.completed_tasks == ("first", "second")
    assert executor.persisted_snapshots == [{"first", "second"}]


@pytest.mark.asyncio
async def test_staff_takeover_preempts_running_workflow() -> None:
    workflow_plan = plan(
        TaskNode(task_id="first", assigned_agent="fast_agent", task_description="first"),
    )
    invocation_context = context()
    executor = MemoryExecutor(
        DurableBlackboard(
            tenant_id=invocation_context.tenant_id,
            conversation_id=invocation_context.conversation_id,
            plan=workflow_plan,
            initial_message="test",
        ),
        registry(),
    )
    executor.preempt = True

    result = await executor.execute(uuid.uuid4(), invocation_context, workflow_plan)

    assert result.status is WorkflowExecutionStatus.preempted
    assert result.completed_tasks == ()


@pytest.mark.asyncio
async def test_workflow_overall_timeout() -> None:
    workflow_plan = plan(
        TaskNode(task_id="first", assigned_agent="slow_agent", task_description="first"),
    )
    invocation_context = context()
    executor = MemoryExecutor(
        DurableBlackboard(
            tenant_id=invocation_context.tenant_id,
            conversation_id=invocation_context.conversation_id,
            plan=workflow_plan,
            initial_message="test",
        ),
        registry(),
    )
    executor.MAX_WORKFLOW_SECONDS = 0.001

    result = await executor.execute(uuid.uuid4(), invocation_context, workflow_plan)

    assert result.status is WorkflowExecutionStatus.failed
    assert result.failure_reason == "workflow_timeout"
