from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import ActorType, AuditEvent
from app.models.conversation import Conversation, ConversationStatus
from app.models.handoff import Handoff, HandoffStatus
from app.models.message import Message, SenderType
from app.models.visitor_session import VisitorSession
from app.prompts.classifier import CLASSIFIER_SYSTEM_PROMPT
from app.providers import LLMMessage, LLMProvider, LLMRequest, get_llm_provider
from app.schemas.assistant import TriageAction, TriageResult

logger = logging.getLogger("mcss.assistant.triage")

MAX_TRIAGE_HISTORY_MESSAGES = 6
MAX_TRIAGE_HISTORY_CHARS = 2000
MAX_CURRENT_MESSAGE_CHARS = 1000


async def classify_message(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    current_message: str,
    llm_provider: LLMProvider | None = None,
) -> TriageResult | None:
    """Classify visitor message into answer, clarify, or handoff.

    Invariants:
    1. NEVER calls LLM if conversation is staff_active or closed.
    2. Constructs input strictly from visitor and assistant history (excludes internal notes).
    3. Caps history and input length.
    4. Parses through strict Pydantic schema; any unknown action or malformed output defaults to handoff.
    5. Persists an audit event with sanitized classification reason and provider metadata (never raw prompt).
    """
    # 1. State check
    conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
    conv_res = await db.execute(conv_stmt)
    conv = conv_res.scalar_one_or_none()
    if not conv:
        return None

    if conv.status != ConversationStatus.open:
        logger.info(
            "Skipping triage: conversation %s status is '%s'",
            conversation_id,
            conv.status.value,
        )
        return None

    h_stmt = select(Handoff.id).where(
        Handoff.conversation_id == conversation_id,
        Handoff.status.in_([HandoffStatus.queued, HandoffStatus.claimed]),
    ).limit(1)
    if (await db.execute(h_stmt)).scalar_one_or_none() is not None:
        logger.info(
            "Skipping triage: conversation %s has active handoff ticket",
            conversation_id,
        )
        return None

    # Fetch active visitor session to evaluate customer identity assurance level
    sess_stmt = (
        select(VisitorSession)
        .where(
            VisitorSession.conversation_id == conversation_id,
            VisitorSession.revoked_at.is_(None),
            VisitorSession.expires_at > datetime.now(UTC),
        )
        .order_by(desc(VisitorSession.created_at))
        .limit(1)
    )
    sess_res = await db.execute(sess_stmt)
    active_session = sess_res.scalar_one_or_none()
    is_authenticated = bool(active_session and active_session.is_customer_authenticated)

    # 2. Fetch bounded conversation history (excluding staff notes and system messages)
    hist_stmt = (
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.sender_type.in_([SenderType.visitor, SenderType.assistant]),
        )
        .order_by(desc(Message.created_at), desc(Message.id))
        .limit(MAX_TRIAGE_HISTORY_MESSAGES)
    )
    hist_res = await db.execute(hist_stmt)
    history_messages = list(reversed(hist_res.scalars().all()))

    # Build prompt messages
    llm_messages: list[LLMMessage] = [
        LLMMessage(role="system", content=CLASSIFIER_SYSTEM_PROMPT.strip())
    ]

    total_chars = 0
    bounded_history: list[LLMMessage] = []
    for msg in reversed(history_messages):
        content = msg.content.strip()
        if total_chars + len(content) > MAX_TRIAGE_HISTORY_CHARS and bounded_history:
            break
        role = "user" if msg.sender_type == SenderType.visitor else "assistant"
        bounded_history.insert(0, LLMMessage(role=role, content=content))
        total_chars += len(content)

    llm_messages.extend(bounded_history)

    # Add current message (bounded)
    bounded_current = current_message.strip()[:MAX_CURRENT_MESSAGE_CHARS]
    # Only append if not already identical to the last message in bounded history
    if not (bounded_history and bounded_history[-1].role == "user" and bounded_history[-1].content == bounded_current):
        llm_messages.append(LLMMessage(role="user", content=bounded_current))

    # 3. Provider call
    provider = llm_provider or get_llm_provider()
    request = LLMRequest(
        messages=llm_messages,
        temperature=0.0,
        max_tokens=120,
        response_format={"type": "json_object"},
        timeout=10.0,
    )

    provider_metadata: dict[str, Any] = {}
    try:
        response = await provider.chat_complete(request)
        provider_metadata = {
            "model": response.model,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "total_tokens": response.total_tokens,
        }

        # 4. Strict Schema Parsing
        raw_data = json.loads(response.content)
        action_str = str(raw_data.get("action", "")).strip().lower()
        reason_str = str(raw_data.get("reason", "unknown"))[:120]

        valid_actions = {
            TriageAction.answer.value,
            TriageAction.clarify.value,
            TriageAction.handoff.value,
            TriageAction.business_data.value,
            TriageAction.out_of_scope.value,
        }

        if action_str not in valid_actions:
            triage_result = TriageResult.fallback_handoff(reason="unknown_action_fallback")
        else:
            parsed_action = TriageAction(action_str)
            # Gated triage policy enforcement:
            if is_authenticated:
                if parsed_action == TriageAction.business_data:
                    triage_result = TriageResult(
                        action=TriageAction.business_data,
                        reason=reason_str or "business_data_request",
                    )
                elif (
                    parsed_action == TriageAction.handoff
                    and reason_str == "account_or_billing_request"
                    and not any(
                        w in current_message.lower()
                        for w in [
                            "cancel",
                            "human",
                            "agent",
                            "representative",
                            "person",
                            "operator",
                            "staff",
                            "stop charging",
                            "change",
                        ]
                    )
                ):
                    triage_result = TriageResult(
                        action=TriageAction.business_data,
                        reason="business_data_request",
                    )
                else:
                    triage_result = TriageResult(
                        action=parsed_action,
                        reason=reason_str,
                    )
            else:
                # Unverified visitor (IAL < 2) requesting business records is prompted to authenticate
                if parsed_action == TriageAction.business_data:
                    triage_result = TriageResult(
                        action=TriageAction.clarify,
                        reason="authentication_required",
                    )
                else:
                    triage_result = TriageResult(
                        action=parsed_action,
                        reason=reason_str,
                    )

    except Exception as exc:
        logger.warning(
            "Triage LLM call or parsing failed for conversation %s: %s; falling back to handoff",
            conversation_id,
            type(exc).__name__,
        )
        provider_metadata["error"] = type(exc).__name__
        triage_result = TriageResult.fallback_handoff(reason="provider_or_parse_failure")

    # 5. Persist audit decision record (sanitized; never raw prompt)
    audit = AuditEvent(
        actor_type=ActorType.system,
        actor_id=None,
        event_type="assistant.triage",
        resource_type="conversation",
        resource_id=conversation_id,
        metadata_json={
            "action": triage_result.action.value,
            "reason": triage_result.reason,
            "provider_metadata": provider_metadata,
        },
    )
    db.add(audit)
    await db.flush()

    return triage_result

