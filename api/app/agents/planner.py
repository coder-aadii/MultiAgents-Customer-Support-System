"""Validated, bounded DAG contracts for Phase 6 investigation planning."""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agents.registry import AgentRegistry


class PlanValidationError(ValueError):
    """Raised when a model-proposed investigation plan violates a hard invariant."""


class PlanningOutcome(StrEnum):
    fast_path = "fast_path"
    plan = "plan"
    handoff = "handoff"


class TaskNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    assigned_agent: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    task_description: str = Field(min_length=1, max_length=500)
    required_inputs: dict[str, Any] = Field(default_factory=dict)
    dependencies: tuple[str, ...] = ()

    @field_validator("dependencies")
    @classmethod
    def dependencies_are_unique(cls, dependencies: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("task dependencies must be unique")
        return dependencies


class InvestigationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_intents: tuple[str, ...] = Field(min_length=1, max_length=6)
    tasks: tuple[TaskNode, ...] = Field(min_length=1, max_length=6)

    @field_validator("user_intents")
    @classmethod
    def user_intents_are_non_empty(cls, intents: tuple[str, ...]) -> tuple[str, ...]:
        if any(not intent.strip() for intent in intents):
            raise ValueError("user intents cannot be empty")
        return intents


class PlanningDecision(BaseModel):
    """Safe output from the supervisor: a plan, a direct specialist route, or handoff."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: PlanningOutcome
    reason: str = Field(min_length=1, max_length=120)
    plan: InvestigationPlan | None = None
    target_agent: str | None = Field(default=None, max_length=64)
    replan_attempt: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def decision_shape_is_safe(self) -> "PlanningDecision":
        if self.outcome is PlanningOutcome.plan and self.plan is None:
            raise ValueError("plan outcome requires an investigation plan")
        if self.outcome is PlanningOutcome.fast_path and not self.target_agent:
            raise ValueError("fast-path outcome requires a target agent")
        if self.outcome is PlanningOutcome.handoff and (self.plan or self.target_agent):
            raise ValueError("handoff outcome cannot contain work to execute")
        return self

    @classmethod
    def handoff(cls, reason: str, replan_attempt: int = 0) -> "PlanningDecision":
        return cls(
            outcome=PlanningOutcome.handoff,
            reason=reason,
            replan_attempt=replan_attempt,
        )


class TaskPlanner:
    """Validates model output before later phases can execute any plan node."""

    MAX_DEPTH = 3
    MAX_PARALLEL_NODES = 4

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def validate(self, plan: InvestigationPlan) -> InvestigationPlan:
        task_by_id = {task.task_id: task for task in plan.tasks}
        if len(task_by_id) != len(plan.tasks):
            raise PlanValidationError("task IDs must be unique")

        for task in plan.tasks:
            if self._registry.get(task.assigned_agent) is None:
                raise PlanValidationError(f"task '{task.task_id}' references an unregistered agent")
            unknown_dependencies = set(task.dependencies) - set(task_by_id)
            if unknown_dependencies:
                raise PlanValidationError(
                    f"task '{task.task_id}' has unknown dependencies: {sorted(unknown_dependencies)}"
                )
            if task.task_id in task.dependencies:
                raise PlanValidationError(f"task '{task.task_id}' cannot depend on itself")

        levels = self._topological_levels(plan.tasks, task_by_id)
        if len(levels) > self.MAX_DEPTH:
            raise PlanValidationError(f"plan exceeds maximum dependency depth of {self.MAX_DEPTH}")
        if any(len(level) > self.MAX_PARALLEL_NODES for level in levels):
            raise PlanValidationError(
                f"plan exceeds maximum parallel nodes of {self.MAX_PARALLEL_NODES}"
            )
        return plan

    def topological_sort(self, plan: InvestigationPlan) -> list[TaskNode]:
        """Return deterministic task order, rejecting cycles before any execution phase."""
        task_by_id = {task.task_id: task for task in plan.tasks}
        self.validate(plan)
        levels = self._topological_levels(plan.tasks, task_by_id)
        return [task for level in levels for task in level]

    def _topological_levels(
        self, tasks: tuple[TaskNode, ...], task_by_id: dict[str, TaskNode]
    ) -> list[list[TaskNode]]:
        indegree = {task.task_id: len(task.dependencies) for task in tasks}
        children: dict[str, list[str]] = defaultdict(list)
        for task in tasks:
            for dependency in task.dependencies:
                children[dependency].append(task.task_id)

        ready = deque(task.task_id for task in tasks if indegree[task.task_id] == 0)
        levels: list[list[TaskNode]] = []
        visited = 0
        while ready:
            current_ids = list(ready)
            ready.clear()
            level = [task_by_id[task_id] for task_id in current_ids]
            levels.append(level)
            visited += len(level)
            for task_id in current_ids:
                for child_id in children[task_id]:
                    indegree[child_id] -= 1
                    if indegree[child_id] == 0:
                        ready.append(child_id)

        if visited != len(tasks):
            raise PlanValidationError("investigation plan contains a dependency cycle")
        return levels
