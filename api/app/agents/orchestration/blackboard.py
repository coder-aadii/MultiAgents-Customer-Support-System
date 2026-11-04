"""In-memory representation of durable, tenant-scoped workflow state."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agents.planner import InvestigationPlan
from app.common.redaction import mask_pii, redact_sensitive_data

MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_CHARS = 2_000


@dataclass(slots=True)
class DurableBlackboard:
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    plan: InvestigationPlan
    initial_message: str
    history: list[str] = field(default_factory=list)
    completed_tasks: set[str] = field(default_factory=set)
    task_results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.initial_message = self.initial_message[:MAX_HISTORY_CHARS]
        self.history = [message[:MAX_HISTORY_CHARS] for message in self.history[-MAX_HISTORY_MESSAGES:]]

    def record_task_result(self, task_id: str, result: dict[str, Any]) -> None:
        if task_id not in {task.task_id for task in self.plan.tasks}:
            raise ValueError(f"task '{task_id}' does not belong to this investigation plan")
        safe_result = mask_pii(redact_sensitive_data(result), exclude_keys=frozenset({"recommendations", "facts"}))
        if not isinstance(safe_result, dict):
            raise ValueError("task result must serialize to an object")
        self.task_results[task_id] = safe_result
        self.completed_tasks.add(task_id)

    def assert_scope(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        """Refuse cross-tenant or cross-conversation blackboard access."""
        if self.tenant_id != tenant_id or self.conversation_id != conversation_id:
            raise PermissionError("blackboard scope mismatch")

    def as_checkpoint_state(self) -> dict[str, Any]:
        return {
            "plan": self.plan.model_dump(mode="json"),
            "initial_message": self.initial_message,
            "history": self.history,
            "task_results": self.task_results,
        }

    @classmethod
    def from_checkpoint(
        cls,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        completed_tasks: list[str],
        state: dict[str, Any],
    ) -> "DurableBlackboard":
        plan_data = state.get("plan")
        if not isinstance(plan_data, dict):
            raise ValueError("workflow checkpoint is missing its plan")
        task_results = state.get("task_results", {})
        return cls(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            plan=InvestigationPlan.model_validate(plan_data),
            initial_message=str(state.get("initial_message", "")),
            history=list(state.get("history", [])),
            completed_tasks=set(completed_tasks),
            task_results=dict(task_results) if isinstance(task_results, dict) else {},
        )
