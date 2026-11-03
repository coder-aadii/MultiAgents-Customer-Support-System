"""Persistence helpers for the Phase 6 agent execution journal.

The journal deliberately records only redacted structured inputs/outputs.  It is
called by the live orchestration boundary, rather than trusting agents to write
their own audit rows.
"""

from __future__ import annotations

from time import monotonic
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.models.agent_execution import AgentExecutionAudit


async def record_agent_execution(
    db: AsyncSession,
    *,
    agent: BaseAgent,
    context: AgentContext,
    started_at: float,
    result: AgentResult | None = None,
    error: Exception | None = None,
) -> None:
    """Append one sanitized execution row in the caller's transaction."""
    output: dict[str, Any]
    if result is not None:
        output = {"result": result.payload, "error_code": result.error_code}
        token_usage = result.token_usage.model_dump(mode="json")
        status = result.status.value
    else:
        # Do not retain exception text: it can include untrusted connector data.
        output = {"error_type": type(error).__name__ if error else "UnknownError"}
        token_usage = {"prompt": 0, "completion": 0, "total": 0}
        status = "failed"
    db.add(
        AgentExecutionAudit(
            tenant_id=context.tenant_id,
            conversation_id=context.conversation_id,
            agent_name=agent.agent_name,
            agent_version=agent.agent_version,
            execution_phase=context.execution_phase,
            input_payload_json=context.payload,
            output_payload_json=output,
            token_usage=token_usage,
            execution_duration_ms=round((monotonic() - started_at) * 1000, 3),
            status=status,
        )
    )
