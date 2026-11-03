from __future__ import annotations

from app.agents.planner import InvestigationPlan, TaskNode
from app.config import settings
from app.services.job_worker import multi_agent_workflow_enabled
from app.services.multi_agent_service import _resolve_task_inputs


def plan(agent: str) -> InvestigationPlan:
    return InvestigationPlan(
        user_intents=("lookup",),
        tasks=(TaskNode(task_id="lookup", assigned_agent=agent, task_description="lookup"),),
    )


def test_resolver_injects_server_derived_shipment_identifier() -> None:
    resolved = _resolve_task_inputs(plan("logistics_specialist"), "Track shipment TRK-1209 please")

    assert resolved is not None
    assert resolved.tasks[0].required_inputs["entity_id"] == "TRK-1209"


def test_resolver_refuses_logistics_without_tracking_identifier() -> None:
    assert _resolve_task_inputs(plan("logistics_specialist"), "Where is my shipment?") is None


def test_resolver_injects_refund_identifier_without_connector_metadata() -> None:
    resolved = _resolve_task_inputs(plan("billing_specialist"), "What is happening with REF-7301?")

    assert resolved is not None
    assert resolved.tasks[0].required_inputs["refund_id"] == "REF-7301"


def test_resolver_refuses_billing_task_without_server_derived_identifier() -> None:
    assert _resolve_task_inputs(plan("billing_specialist"), "Why was I charged?") is None


def test_workflow_kill_switch_requires_both_flags(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MCSS_MULTI_AGENT_ORCHESTRATION_ENABLED", True)
    monkeypatch.setattr(settings, "MCSS_MULTI_AGENT_WORKFLOW_ENABLED", False)
    assert multi_agent_workflow_enabled() is False

    monkeypatch.setattr(settings, "MCSS_MULTI_AGENT_WORKFLOW_ENABLED", True)
    assert multi_agent_workflow_enabled() is True
