"""Bounded, checkpointed execution of validated multi-agent investigation DAGs."""

from __future__ import annotations

import asyncio
import uuid
from time import monotonic
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Callable

from sqlalchemy import select
from app.agents.base import AgentContext, BaseAgent
from app.agents.audit import record_agent_execution
from app.agents.orchestration.blackboard import DurableBlackboard
from app.agents.planner import InvestigationPlan, TaskPlanner
from app.agents.registry import AgentRegistry, default_agent_registry
from app.config import settings
from app.models.assistant_job import AssistantJob, AssistantJobStatus
from app.models.conversation import Conversation, ConversationStatus
from app.models.workflow_checkpoint import WorkflowCheckpoint, WorkflowCheckpointStatus


class WorkflowExecutionStatus(StrEnum):
    completed = "completed"
    failed = "failed"
    preempted = "preempted"


class WorkflowPreemptedError(RuntimeError):
    """Raised internally when staff ownership makes all AI work invalid."""


@dataclass(frozen=True, slots=True)
class WorkflowExecutionResult:
    status: WorkflowExecutionStatus
    checkpoint_id: uuid.UUID
    completed_tasks: tuple[str, ...]
    failure_reason: str | None = None


class MultiAgentWorkflowExecutor:
    """Executes a validated plan with max-four fanout and node-level durability."""

    MAX_CONCURRENT_TASKS = 4
    MAX_WORKFLOW_SECONDS = 15.0

    def __init__(
        self,
        db_factory: Callable[[], Any],
        registry: AgentRegistry | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._db_factory = db_factory
        self._registry = registry or default_agent_registry
        self._enabled = settings.MCSS_MULTI_AGENT_WORKFLOW_ENABLED if enabled is None else enabled
        self._planner = TaskPlanner(self._registry)

    async def execute(
        self,
        job_id: uuid.UUID,
        context: AgentContext,
        plan: InvestigationPlan | None = None,
    ) -> WorkflowExecutionResult:
        """Resume a checkpoint or start a new plan; never execute after staff takeover."""
        if not self._enabled:
            raise RuntimeError("multi-agent workflow execution is disabled")
        blackboard, checkpoint_id = await self._load_or_create(job_id, context, plan)
        checkpoint_lock = asyncio.Lock()
        try:
            return await asyncio.wait_for(
                self._execute_bounded(job_id, context, blackboard, checkpoint_id, checkpoint_lock),
                timeout=self.MAX_WORKFLOW_SECONDS,
            )
        except TimeoutError:
            await self._set_terminal_status(job_id, checkpoint_id, WorkflowCheckpointStatus.failed)
            return WorkflowExecutionResult(
                status=WorkflowExecutionStatus.failed,
                checkpoint_id=checkpoint_id,
                completed_tasks=tuple(sorted(blackboard.completed_tasks)),
                failure_reason="workflow_timeout",
            )

    async def _execute_bounded(
        self,
        job_id: uuid.UUID,
        context: AgentContext,
        blackboard: DurableBlackboard,
        checkpoint_id: uuid.UUID,
        checkpoint_lock: asyncio.Lock,
    ) -> WorkflowExecutionResult:
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_TASKS)
        while len(blackboard.completed_tasks) < len(blackboard.plan.tasks):
            if await self._preempt_if_staff_taken_over(job_id, checkpoint_id, context):
                return WorkflowExecutionResult(
                    status=WorkflowExecutionStatus.preempted,
                    checkpoint_id=checkpoint_id,
                    completed_tasks=tuple(sorted(blackboard.completed_tasks)),
                )
            ready_tasks = [
                task
                for task in blackboard.plan.tasks
                if task.task_id not in blackboard.completed_tasks
                and set(task.dependencies).issubset(blackboard.completed_tasks)
            ]
            if not ready_tasks:
                await self._set_terminal_status(job_id, checkpoint_id, WorkflowCheckpointStatus.failed)
                return WorkflowExecutionResult(
                    status=WorkflowExecutionStatus.failed,
                    checkpoint_id=checkpoint_id,
                    completed_tasks=tuple(sorted(blackboard.completed_tasks)),
                    failure_reason="no_executable_tasks",
                )

            tasks = [
                self._run_task(
                    semaphore,
                    checkpoint_lock,
                    job_id,
                    context,
                    blackboard,
                    checkpoint_id,
                    task.task_id,
                )
                for task in ready_tasks
            ]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            if any(isinstance(outcome, WorkflowPreemptedError) for outcome in outcomes):
                return WorkflowExecutionResult(
                    status=WorkflowExecutionStatus.preempted,
                    checkpoint_id=checkpoint_id,
                    completed_tasks=tuple(sorted(blackboard.completed_tasks)),
                )
            failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
            if failures:
                await self._set_terminal_status(job_id, checkpoint_id, WorkflowCheckpointStatus.failed)
                return WorkflowExecutionResult(
                    status=WorkflowExecutionStatus.failed,
                    checkpoint_id=checkpoint_id,
                    completed_tasks=tuple(sorted(blackboard.completed_tasks)),
                    failure_reason=type(failures[0]).__name__,
                )

        await self._set_terminal_status(job_id, checkpoint_id, WorkflowCheckpointStatus.completed)
        return WorkflowExecutionResult(
            status=WorkflowExecutionStatus.completed,
            checkpoint_id=checkpoint_id,
            completed_tasks=tuple(sorted(blackboard.completed_tasks)),
        )

    async def _run_task(
        self,
        semaphore: asyncio.Semaphore,
        checkpoint_lock: asyncio.Lock,
        job_id: uuid.UUID,
        context: AgentContext,
        blackboard: DurableBlackboard,
        checkpoint_id: uuid.UUID,
        task_id: str,
    ) -> None:
        async with semaphore:
            if await self._preempt_if_staff_taken_over(job_id, checkpoint_id, context):
                raise WorkflowPreemptedError()
            task = next(task for task in blackboard.plan.tasks if task.task_id == task_id)
            agent_class = self._registry.get(task.assigned_agent)
            if agent_class is None:
                raise RuntimeError(f"registered agent '{task.assigned_agent}' is unavailable")
            agent: BaseAgent = agent_class()
            # Each parallel node owns a database session.  Sharing the request
            # session across asyncio.gather tasks is unsafe and could mix audit
            # or connector state between specialists.
            task_session = self._db_factory()
            # Lightweight unit executors intentionally provide no persistence
            # factory; production always supplies an AsyncSession factory.
            if task_session is None:
                result = await agent.execute(AgentContext(
                    tenant_id=context.tenant_id,
                    conversation_id=context.conversation_id,
                    execution_phase="workflow_task",
                    payload={
                        **context.payload, **task.required_inputs, "task_id": task.task_id,
                        "task_description": task.task_description,
                        "dependency_results": {dependency: blackboard.task_results.get(dependency, {}) for dependency in task.dependencies},
                    },
                    verified_customer_id=context.verified_customer_id, budget=context.budget,
                    db=context.db, visitor_session=context.visitor_session,
                ))
            else:
                async with task_session as task_db:
                    task_context = AgentContext(
                    tenant_id=context.tenant_id,
                    conversation_id=context.conversation_id,
                    execution_phase="workflow_task",
                    payload={
                        **context.payload,
                        **task.required_inputs,
                        "task_id": task.task_id,
                        "task_description": task.task_description,
                        "dependency_results": {
                            dependency: blackboard.task_results.get(dependency, {})
                            for dependency in task.dependencies
                        },
                    },
                    verified_customer_id=context.verified_customer_id,
                    budget=context.budget,
                    db=task_db,
                    visitor_session=context.visitor_session,
                )
                    started_at = monotonic()
                    try:
                        result = await agent.execute(task_context)
                    except Exception as exc:
                        await record_agent_execution(
                            task_db, agent=agent, context=task_context, started_at=started_at, error=exc
                        )
                        await task_db.commit()
                        raise
                    await record_agent_execution(
                        task_db, agent=agent, context=task_context, started_at=started_at, result=result
                    )
                    await task_db.commit()
            serialized_result = {
                "status": result.status,
                "payload": result.payload,
                "token_usage": result.token_usage.model_dump(mode="json"),
                "tool_calls": result.tool_calls,
            }
            async with checkpoint_lock:
                if await self._preempt_if_staff_taken_over(job_id, checkpoint_id, context):
                    raise WorkflowPreemptedError()
                blackboard.record_task_result(task_id, serialized_result)
                if not await self._persist_progress(checkpoint_id, blackboard):
                    raise WorkflowPreemptedError()

    async def _load_or_create(
        self,
        job_id: uuid.UUID,
        context: AgentContext,
        plan: InvestigationPlan | None,
    ) -> tuple[DurableBlackboard, uuid.UUID]:
        async with self._db_factory() as db:
            job = await db.scalar(select(AssistantJob).where(AssistantJob.id == job_id).with_for_update())
            if job is None:
                raise ValueError("assistant job not found")
            if job.conversation_id != context.conversation_id or job.organization_id != context.tenant_id:
                raise ValueError("workflow context does not match its assistant job")

            checkpoint = await db.scalar(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.job_id == job_id)
                .with_for_update()
            )
            if checkpoint is not None:
                if checkpoint.tenant_id != context.tenant_id:
                    raise ValueError("workflow checkpoint tenant mismatch")
                blackboard = DurableBlackboard.from_checkpoint(
                    tenant_id=context.tenant_id,
                    conversation_id=context.conversation_id,
                    completed_tasks=checkpoint.completed_tasks,
                    state=checkpoint.blackboard_state,
                )
                await db.commit()
                return blackboard, checkpoint.id

            if plan is None:
                raise ValueError("a new workflow requires an investigation plan")
            self._planner.validate(plan)
            blackboard = DurableBlackboard(
                tenant_id=context.tenant_id,
                conversation_id=context.conversation_id,
                plan=plan,
                initial_message=str(context.payload.get("user_message", "")),
                history=list(context.payload.get("history", [])),
            )
            checkpoint = WorkflowCheckpoint(
                job_id=job_id,
                conversation_id=context.conversation_id,
                tenant_id=context.tenant_id,
                plan_id=plan.plan_id,
                completed_tasks=[],
                blackboard_state=blackboard.as_checkpoint_state(),
                status=WorkflowCheckpointStatus.running.value,
            )
            db.add(checkpoint)
            await db.commit()
            return blackboard, checkpoint.id

    async def _persist_progress(self, checkpoint_id: uuid.UUID, blackboard: DurableBlackboard) -> bool:
        async with self._db_factory() as db:
            checkpoint = await db.scalar(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.id == checkpoint_id)
                .with_for_update()
            )
            if checkpoint is None or checkpoint.status != WorkflowCheckpointStatus.running.value:
                await db.rollback()
                return False
            checkpoint.completed_tasks = sorted(blackboard.completed_tasks)
            checkpoint.blackboard_state = blackboard.as_checkpoint_state()
            checkpoint.current_step_index = len(blackboard.completed_tasks)
            checkpoint.updated_at = datetime.now(UTC)
            await db.commit()
            return True

    async def _preempt_if_staff_taken_over(
        self, job_id: uuid.UUID, checkpoint_id: uuid.UUID, context: AgentContext
    ) -> bool:
        async with self._db_factory() as db:
            conversation = await db.scalar(
                select(Conversation)
                .where(Conversation.id == context.conversation_id)
                .with_for_update()
            )
            if conversation is None or conversation.status not in (
                ConversationStatus.staff_active,
                ConversationStatus.closed,
            ):
                await db.rollback()
                return False
            checkpoint = await db.scalar(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.id == checkpoint_id)
                .with_for_update()
            )
            job = await db.scalar(select(AssistantJob).where(AssistantJob.id == job_id).with_for_update())
            if checkpoint is not None:
                checkpoint.status = WorkflowCheckpointStatus.preempted.value
                checkpoint.updated_at = datetime.now(UTC)
            if job is not None:
                job.status = AssistantJobStatus.discarded.value
                job.completed_at = datetime.now(UTC)
            await db.commit()
            return True

    async def _set_terminal_status(
        self, job_id: uuid.UUID, checkpoint_id: uuid.UUID, status: WorkflowCheckpointStatus
    ) -> None:
        async with self._db_factory() as db:
            checkpoint = await db.scalar(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.id == checkpoint_id)
                .with_for_update()
            )
            if checkpoint is not None:
                checkpoint.status = status.value
                checkpoint.updated_at = datetime.now(UTC)
            # Workflow completion is deliberately distinct from job completion.
            # The outer worker owns the latter only after synthesis and the
            # customer response (or safe handoff) have committed.  Otherwise a
            # crash in that window would make the job unreclaimable.
            await db.commit()
