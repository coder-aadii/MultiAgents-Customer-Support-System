"""Feature-gated production boundary for the durable Phase 6 workflow.

This module is intentionally invoked only by ``job_worker``.  It owns the
server-authenticated context, keeps the legacy pipeline as the rollback path,
and performs the final staff-takeover check before an answer is persisted.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from time import monotonic

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.audit import record_agent_execution
from app.agents.base import AgentContext
from app.agents.orchestration.aggregator import EvidenceAggregator
from app.agents.orchestration.blackboard import DurableBlackboard
from app.agents.orchestration.executor import MultiAgentWorkflowExecutor, WorkflowExecutionStatus
from app.agents.orchestration.synthesizer import MultiAgentSynthesisCoordinator, ResolutionStatus
from app.agents.planner import InvestigationPlan, PlanningDecision, PlanningOutcome, TaskNode
from app.agents.specialists.schemas import SpecialistFindings
from app.agents.supervisor import SupervisorAgent, is_identity_query
from app.config import settings
from app.database import async_session_factory
from app.models.conversation import Conversation, ConversationStatus
from app.models.handoff import Handoff, HandoffReason, HandoffStatus
from app.models.message import Message, SenderType
from app.models.visitor_session import VisitorSession
from app.models.workflow_checkpoint import WorkflowCheckpoint
from app.services.assistant_service import (
    HANDOFF_MESSAGE_TEXT,
    OUT_OF_SCOPE_MESSAGE_TEXT,
    create_or_reuse_handoff,
)


async def handle_multi_agent_pipeline(
    *,
    db: AsyncSession,
    job_id: uuid.UUID,
    conversation_id: uuid.UUID,
    visitor_message_content: str,
) -> Message | None:
    """Run one durable, evidence-grounded workflow and persist a safe response."""
    now = datetime.now(UTC)
    conversation = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    if conversation is None or conversation.status != ConversationStatus.open:
        return None

    has_handoff = await db.scalar(
        select(Handoff.id).where(
            Handoff.conversation_id == conversation_id,
            Handoff.status.in_([HandoffStatus.queued, HandoffStatus.claimed]),
        ).limit(1)
    )
    if has_handoff is not None:
        return None

    clean_msg = re.sub(r"[^\w\s]", "", visitor_message_content.strip().lower()).strip()
    greetings = {
        "hi", "hello", "hey", "hey there", "hi there", "hello there",
        "good morning", "good afternoon", "good evening", "howdy", "greetings", "yo",
    }
    if clean_msg in greetings:
        active = await db.scalar(select(Conversation).where(Conversation.id == conversation.id).with_for_update())
        if active is None or active.status != ConversationStatus.open:
            await db.commit()
            return None
        now_msg = datetime.now(UTC)
        active.last_message_at = now_msg
        active.updated_at = now_msg
        greeting_msg = Message(
            organization_id=active.organization_id,
            conversation_id=active.id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.assistant,
            content=(
                "Hello! How can I help you today? You can ask about our return and refund policies, "
                "track a shipment, or look up your orders."
            ),
            metadata_json={"action": "answer", "reason": "greeting", "sources": []},
            created_at=now_msg,
        )
        db.add(greeting_msg)
        await db.commit()
        return greeting_msg

    off_topic_patterns = [
        r"^(what('s| is) (the )?(date|time|weather|day))",
        r"what is the date",
        r"weather today",
        r"^tell me a joke",
        r"^who is the president",
        r"^how are you",
    ]
    if any(re.search(pat, clean_msg) for pat in off_topic_patterns):
        active = await db.scalar(select(Conversation).where(Conversation.id == conversation.id).with_for_update())
        if active is None or active.status != ConversationStatus.open:
            await db.commit()
            return None
        now_msg = datetime.now(UTC)
        active.last_message_at = now_msg
        active.updated_at = now_msg
        out_msg = Message(
            organization_id=active.organization_id,
            conversation_id=active.id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.assistant,
            content=OUT_OF_SCOPE_MESSAGE_TEXT,
            metadata_json={"action": "out_of_scope", "reason": "off_topic_pattern"},
            created_at=now_msg,
        )
        db.add(out_msg)
        await db.commit()
        return out_msg

    cross_user_patterns = [
        r"\b(?:other|another|someone else'?s|somebody else'?s|customer\s+[\w.-]+|user\s+[\w.-]+)\b.*?\b(?:order|account|data|tracking|details|profile|history)\b",
        r"\b(?:order|account|data|tracking|details)\b.*?\b(?:for|of)\s+(?:another|other|someone else|customer\s+[\w.-]+|user\s+[\w.-]+)\b",
    ]
    if any(re.search(pat, visitor_message_content.lower()) for pat in cross_user_patterns):
        active = await db.scalar(select(Conversation).where(Conversation.id == conversation.id).with_for_update())
        if active is None or active.status != ConversationStatus.open:
            await db.commit()
            return None
        now_msg = datetime.now(UTC)
        active.last_message_at = now_msg
        active.updated_at = now_msg
        privacy_msg = Message(
            organization_id=active.organization_id,
            conversation_id=active.id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.assistant,
            content=(
                "For security and privacy reasons, I can only provide information for orders and accounts directly "
                "associated with your verified login. If you need assistance with a different account or order, please "
                "have the account holder contact us directly, or let me know if you would like to speak with a human support agent."
            ),
            metadata_json={"action": "answer", "reason": "privacy_fence", "sources": []},
            created_at=now_msg,
        )
        db.add(privacy_msg)
        await db.commit()
        return privacy_msg
    session = await db.scalar(
        select(VisitorSession)
        .where(
            VisitorSession.conversation_id == conversation_id,
            VisitorSession.organization_id == conversation.organization_id,
            VisitorSession.revoked_at.is_(None),
            VisitorSession.expires_at > now,
        )
        .order_by(desc(VisitorSession.created_at))
        .limit(1)
    )

    is_authenticated = bool(session and session.is_customer_authenticated and session.verified_customer_id)
    if not is_authenticated:
        norm_msg = visitor_message_content.lower()
        is_personal_query = (
            any(
                phrase in norm_msg
                for phrase in (
                    "my order",
                    "my recent",
                    "recent orders",
                    "last order",
                    "is my order",
                    "where is my",
                    "track my",
                    "list my",
                    "show my",
                    "my account",
                    "my profile",
                    "my details",
                    "my refund",
                    "my shipment",
                )
            )
            or is_identity_query(norm_msg)
        )
        if is_personal_query:
            active = await db.scalar(select(Conversation).where(Conversation.id == conversation.id).with_for_update())
            if active is None or active.status != ConversationStatus.open:
                await db.commit()
                return None
            now_msg = datetime.now(UTC)
            active.last_message_at = now_msg
            active.updated_at = now_msg
            auth_msg = Message(
                organization_id=active.organization_id,
                conversation_id=active.id,
                client_message_id=uuid.uuid4(),
                sender_type=SenderType.assistant,
                content=(
                    "To view, track, or manage your orders and account details, please log in to your account first. "
                    "You can sign in using the **1-Click Customer Switcher** or the login form on this page to securely view your orders. "
                    "If you have general questions about our return policy or shipping procedures, feel free to ask!"
                ),
                metadata_json={"action": "clarify", "reason": "authentication_required", "sources": []},
                created_at=now_msg,
            )
            db.add(auth_msg)
            await db.commit()
            return auth_msg
    context = AgentContext(
        tenant_id=conversation.organization_id,
        conversation_id=conversation_id,
        execution_phase="supervisor",
        payload={"user_message": visitor_message_content},
        verified_customer_id=session.verified_customer_id if session else None,
        db=db,
        visitor_session=session,
    )
    supervisor = SupervisorAgent(enabled=True)
    started_at = monotonic()
    try:
        supervisor_result = await supervisor.execute(context)
        decision = PlanningDecision.model_validate(supervisor_result.payload)
        await record_agent_execution(
            db, agent=supervisor, context=context, started_at=started_at,
            result=supervisor_result,
        )
        await db.commit()
    except Exception as exc:
        await record_agent_execution(db, agent=supervisor, context=context, started_at=started_at, error=exc)
        await db.commit()
        return await _handoff(db, conversation, "supervisor_failure")

    if decision.outcome is PlanningOutcome.handoff:
        return await _handoff(db, conversation, f"supervisor:{decision.reason}")
    plan = decision.plan if decision.outcome is PlanningOutcome.plan else _fast_path_plan(decision.target_agent, visitor_message_content)
    plan = _resolve_task_inputs(plan, visitor_message_content) if plan is not None else None
    if plan is None:
        return await _handoff(db, conversation, "invalid_supervisor_fast_path")

    executor = MultiAgentWorkflowExecutor(async_session_factory)
    execution = await executor.execute(job_id, context, plan)
    if execution.status is WorkflowExecutionStatus.preempted:
        return None
    if execution.status is not WorkflowExecutionStatus.completed:
        return await _handoff(db, conversation, execution.failure_reason or "workflow_failed")

    checkpoint = await db.scalar(
        select(WorkflowCheckpoint).where(
            WorkflowCheckpoint.id == execution.checkpoint_id,
            WorkflowCheckpoint.tenant_id == context.tenant_id,
            WorkflowCheckpoint.conversation_id == conversation_id,
        )
    )
    if checkpoint is None:
        return await _handoff(db, conversation, "workflow_checkpoint_missing")
    blackboard = DurableBlackboard.from_checkpoint(
        context.tenant_id, conversation_id, checkpoint.completed_tasks, checkpoint.blackboard_state
    )
    findings: list[SpecialistFindings] = []
    try:
        for serialized in blackboard.task_results.values():
            payload = serialized.get("payload")
            if isinstance(payload, dict):
                findings.append(SpecialistFindings.model_validate(payload))
    except ValueError:
        return await _handoff(db, conversation, "invalid_specialist_output")
    if not findings:
        return await _handoff(db, conversation, "empty_specialist_output")

    evidence = EvidenceAggregator().aggregate(context.tenant_id, conversation_id, findings)
    coordinator = MultiAgentSynthesisCoordinator(
        reflection_enabled=settings.MCSS_REFLECTION_ENABLED
    )
    resolution = await coordinator.synthesize(context, findings, evidence, db=db)
    if resolution.status is ResolutionStatus.handoff_required:
        return await _handoff(db, conversation, resolution.reason or "resolution_handoff")
    if resolution.status is ResolutionStatus.replan_required:
        return await _handoff(db, conversation, "resolution_replan_required")
    if resolution.response_text is None:
        return await _handoff(db, conversation, "resolution_empty")
    return await _persist_response(db, conversation_id, conversation.organization_id, resolution.response_text, resolution)


def _fast_path_plan(target_agent: str | None, user_message: str) -> InvestigationPlan | None:
    if not target_agent:
        return None
    return InvestigationPlan(
        user_intents=("single_intent",),
        tasks=(TaskNode(
            task_id="primary_investigation",
            assigned_agent=target_agent,
            task_description="Investigate the customer request using only authorized read-only data.",
            required_inputs={"query": user_message},
        ),),
    )


_ORDER_ID = re.compile(r"\b(?:ORD-[0-9A-Za-z]{3,32}|[0-9a-fA-F]{32}|#[0-9A-Za-z]{2,32}|\d{3,16})\b", re.I)
_SHIPMENT_ID = re.compile(r"\b(?:TRK|SHP|BR)-[0-9A-Z]{3,32}\b", re.I)
_REFUND_ID = re.compile(r"\bREF-[0-9A-Z]{3,32}\b", re.I)


def _resolve_task_inputs(plan: InvestigationPlan, message: str) -> InvestigationPlan | None:
    """Inject and validate server-derived IDs; never ask a planner to invent them."""
    def match(pattern: re.Pattern[str]) -> str | None:
        found = pattern.search(message)
        return found.group(0).upper() if found else None

    resolved: list[TaskNode] = []
    for task in plan.tasks:
        inputs = dict(task.required_inputs)
        inputs.setdefault("query", message)
        if task.assigned_agent == "business_data_specialist":
            norm_desc = (task.task_description or "").lower()
            norm_msg = message.lower()
            if is_identity_query(norm_msg) or is_identity_query(norm_desc):
                inputs["entity_type"] = "customer"
            else:
                inputs.setdefault("entity_type", "order")
                order_id = match(_ORDER_ID)
                if order_id:
                    inputs["entity_id"] = order_id.lstrip("#")
        elif task.assigned_agent == "logistics_specialist":
            shipment_id = match(_SHIPMENT_ID)
            if shipment_id is not None:
                inputs["entity_id"] = shipment_id
            elif "order" in message.lower() or match(_ORDER_ID):
                # Fall back to business_data_specialist if order query lacked carrier tracking code
                task = task.model_copy(update={
                    "assigned_agent": "business_data_specialist",
                    "task_description": "Investigate order and shipment status using authorized read-only data.",
                })
                inputs["entity_type"] = "order"
                order_id = match(_ORDER_ID)
                if order_id:
                    inputs["entity_id"] = order_id.lstrip("#")
            else:
                return None
        elif task.assigned_agent == "billing_specialist":
            refund_id = match(_REFUND_ID)
            if refund_id is None:
                return None
            inputs["refund_id"] = refund_id
        resolved.append(task.model_copy(update={"required_inputs": inputs}))
    return plan.model_copy(update={"tasks": tuple(resolved)})


async def _persist_response(
    db: AsyncSession, conversation_id: uuid.UUID, tenant_id: uuid.UUID, content: str, resolution: object
) -> Message | None:
    conversation = await db.scalar(select(Conversation).where(Conversation.id == conversation_id).with_for_update())
    if conversation is None or conversation.status != ConversationStatus.open:
        await db.commit()
        return None

    has_handoff = await db.scalar(
        select(Handoff.id).where(
            Handoff.conversation_id == conversation_id,
            Handoff.status.in_([HandoffStatus.queued, HandoffStatus.claimed]),
        ).limit(1)
    )
    if has_handoff is not None:
        await db.commit()
        return None
    now = datetime.now(UTC)
    conversation.last_message_at = now
    conversation.updated_at = now
    citations = getattr(resolution, "citations", ())
    message = Message(
        organization_id=tenant_id,
        conversation_id=conversation_id,
        client_message_id=uuid.uuid4(),
        sender_type=SenderType.assistant,
        content=content,
        metadata_json={"action": "answer", "sources": [item.model_dump(mode="json") for item in citations]},
        created_at=now,
    )
    db.add(message)
    await db.commit()
    return message


async def _handoff(db: AsyncSession, conversation: Conversation, reason: str) -> Message | None:
    active = await db.scalar(select(Conversation).where(Conversation.id == conversation.id).with_for_update())
    if active is None or active.status != ConversationStatus.open:
        await db.commit()
        return None
    await create_or_reuse_handoff(
        db, conversation_id=active.id, reason=HandoffReason.policy, summary=f"Phase 6 workflow halted: {reason}"[:500]
    )
    now = datetime.now(UTC)
    active.status = ConversationStatus.waiting_for_staff
    active.last_message_at = now
    active.updated_at = now
    message = Message(
        organization_id=active.organization_id,
        conversation_id=active.id,
        client_message_id=uuid.uuid4(),
        sender_type=SenderType.assistant,
        content=HANDOFF_MESSAGE_TEXT,
        metadata_json={
            "action": "handoff",
            "reason": "phase_6_safe_fallback",
            "staff_diagnostic": {
                "failure_stage": "multi_agent_orchestration",
                "code": "WORKFLOW_HALTED",
                "detail": f"Phase 6 workflow halted: {reason}"[:500],
            },
        },
        created_at=now,
    )
    db.add(message)
    await db.commit()
    return message
