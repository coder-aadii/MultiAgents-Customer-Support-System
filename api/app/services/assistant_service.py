from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_event import ActorType, AuditEvent
from app.models.conversation import Conversation, ConversationStatus
from app.models.handoff import Handoff, HandoffReason, HandoffStatus
from app.models.message import Message, SenderType
from app.prompts.generator import GENERATOR_SYSTEM_PROMPT
from app.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    get_llm_provider,
)
from app.schemas.assistant import GeneratorAction, GeneratorResult, TriageAction, TriageResult
from app.schemas.retrieval import RetrievalResultItem
from app.services.conversation_service import record_audit
from app.services.retrieval_service import retrieve_knowledge_chunks
from app.services.triage_service import classify_message

logger = logging.getLogger("mcss.assistant.service")

HANDOFF_MESSAGE_TEXT = "I’m connecting you with our support team so they can help further."
MIN_CONFIDENCE_THRESHOLD = 0.75
MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_CHARS = 2500
MAX_SOURCES_CHARS = 4500


def map_triage_to_handoff_reason(triage_reason: str) -> HandoffReason:
    """Map triage decision reasons to database HandoffReason enums."""
    if "human" in triage_reason:
        return HandoffReason.visitor_requested
    if any(k in triage_reason for k in ["refund", "order", "billing", "account"]):
        return HandoffReason.policy
    if "unsafe" in triage_reason:
        return HandoffReason.policy
    return HandoffReason.unsupported_request


async def create_or_reuse_handoff(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    reason: HandoffReason,
    summary: str,
) -> Handoff:
    """Idempotently create or retrieve the single queued/claimed handoff for a conversation."""
    stmt = (
        select(Handoff)
        .where(
            Handoff.conversation_id == conversation_id,
            Handoff.status.in_([HandoffStatus.queued, HandoffStatus.claimed]),
        )
        .with_for_update()
    )
    res = await db.execute(stmt)
    handoff = res.scalar_one_or_none()
    if not handoff:
        handoff = Handoff(
            conversation_id=conversation_id,
            reason=reason,
            summary=summary[:500],
            status=HandoffStatus.queued,
        )
        db.add(handoff)
        await db.flush()
    return handoff


async def generate_grounded_answer(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    visitor_query: str,
    retrieved_chunks: list[RetrievalResultItem],
    llm_provider: LLMProvider | None = None,
) -> GeneratorResult:
    """Generate structured, cited answer strictly from supplied retrieved published chunks.

    Enforces:
    1. Sources formatted with explicit Chunk UUIDs and titles, capped at 4,500 chars.
    2. Up to 6 messages of conversational history (excluding staff notes).
    3. Strict JSON schema parsing with exactly one repair retry on malformed JSON.
    4. Exact citation verification: every cited ID must belong to retrieved_chunks (no hallucinations).
    5. Confidence heuristic check (must be >= 0.75).
    """
    if not retrieved_chunks:
        return GeneratorResult.fallback_handoff(reason="no_evidence_available")

    valid_chunk_map: dict[uuid.UUID, RetrievalResultItem] = {
        item.chunk_id: item for item in retrieved_chunks
    }

    # 1. Format SOURCES block
    sources_lines = ["SOURCES:"]
    total_source_chars = 0
    for chunk in retrieved_chunks:
        line = f"[Source {chunk.chunk_id}] Title: {chunk.document_title}\n{chunk.content.strip()}\n"
        if total_source_chars + len(line) > MAX_SOURCES_CHARS and len(sources_lines) > 1:
            break
        sources_lines.append(line)
        total_source_chars += len(line)
    sources_text = "\n".join(sources_lines)

    # 2. Format limited conversation history
    hist_stmt = (
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.sender_type.in_([SenderType.visitor, SenderType.assistant]),
        )
        .order_by(desc(Message.created_at), desc(Message.id))
        .limit(MAX_HISTORY_MESSAGES)
    )
    hist_res = await db.execute(hist_stmt)
    history_messages = list(reversed(hist_res.scalars().all()))

    llm_messages: list[LLMMessage] = [
        LLMMessage(role="system", content=GENERATOR_SYSTEM_PROMPT.strip()),
        LLMMessage(role="system", content=sources_text.strip()),
    ]

    total_chars = 0
    bounded_history: list[LLMMessage] = []
    for msg in reversed(history_messages):
        content = msg.content.strip()
        if total_chars + len(content) > MAX_HISTORY_CHARS and bounded_history:
            break
        role = "user" if msg.sender_type == SenderType.visitor else "assistant"
        bounded_history.insert(0, LLMMessage(role=role, content=content))
        total_chars += len(content)

    llm_messages.extend(bounded_history)

    # Current query
    bounded_current = visitor_query.strip()[:1000]
    if not (bounded_history and bounded_history[-1].role == "user" and bounded_history[-1].content == bounded_current):
        llm_messages.append(LLMMessage(role="user", content=bounded_current))

    provider = llm_provider or get_llm_provider()

    # 3. Call LLM with one repair retry attempt on schema violation
    generator_result: GeneratorResult | None = None
    for attempt in range(2):
        if attempt == 1:
            llm_messages.append(
                LLMMessage(
                    role="user",
                    content="Your previous response did not follow the required JSON schema. Return valid JSON only adhering strictly to the schema.",
                )
            )

        req = LLMRequest(
            messages=llm_messages,
            temperature=0.0,
            max_tokens=512,
            response_format={"type": "json_object"},
            timeout=15.0,
        )

        try:
            res = await provider.chat_complete(req)
            data = json.loads(res.content)
            action_val = str(data.get("action", "")).strip().lower()

            if action_val == GeneratorAction.handoff.value:
                return GeneratorResult(
                    action=GeneratorAction.handoff,
                    reason=str(data.get("reason", "unsupported"))[:120],
                )

            if action_val == GeneratorAction.answer.value:
                # Parse cited chunk IDs
                raw_citations = data.get("cited_chunk_ids", [])
                parsed_citations: list[uuid.UUID] = []
                for cid in raw_citations:
                    parsed_citations.append(uuid.UUID(str(cid)))

                generator_result = GeneratorResult(
                    action=GeneratorAction.answer,
                    answer=data.get("answer"),
                    cited_chunk_ids=parsed_citations,
                    confidence=float(data.get("confidence", 0.0)),
                )
                break  # Successfully parsed schema
        except Exception as exc:
            logger.warning(
                "Answer generator attempt %d failed for conversation %s: %s",
                attempt + 1,
                conversation_id,
                type(exc).__name__,
            )

    if not generator_result or generator_result.action != GeneratorAction.answer:
        return GeneratorResult.fallback_handoff(reason="schema_verification_failed")

    # 4. Strict Citation & Grounding Verification
    cited = generator_result.cited_chunk_ids
    if not cited:
        logger.info("Generator returned answer without citations; forcing handoff.")
        return GeneratorResult.fallback_handoff(reason="missing_citations")

    # Check for duplicate citations
    if len(cited) != len(set(cited)):
        logger.info("Generator returned duplicate citations; forcing handoff.")
        return GeneratorResult.fallback_handoff(reason="duplicate_citations")

    # Reject fabricated / unretrieved citations
    for cid in cited:
        if cid not in valid_chunk_map:
            logger.warning(
                "Generator cited unknown chunk %s not in retrieved set %s; forcing handoff.",
                cid,
                list(valid_chunk_map.keys()),
            )
            return GeneratorResult.fallback_handoff(reason="fabricated_citation")

    # Confidence check
    if generator_result.confidence < MIN_CONFIDENCE_THRESHOLD:
        logger.info(
            "Generator self-reported confidence %.2f below threshold %.2f; forcing handoff.",
            generator_result.confidence,
            MIN_CONFIDENCE_THRESHOLD,
        )
        return GeneratorResult.fallback_handoff(reason="low_confidence")

    return generator_result


_conversation_locks: dict[uuid.UUID, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _get_conversation_lock(conversation_id: uuid.UUID) -> asyncio.Lock:
    async with _locks_guard:
        if conversation_id not in _conversation_locks:
            _conversation_locks[conversation_id] = asyncio.Lock()
        return _conversation_locks[conversation_id]


async def handle_assistant_pipeline(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    visitor_message_content: str,
    llm_provider: LLMProvider | None = None,
) -> Message | None:
    """Execute the end-to-end controlled assistant pipeline for a new visitor message.

    Invariants:
    1. Serializes assistant generation per conversation using an async lock.
    2. Does NOT hold PostgreSQL row locks across slow remote LLM and retrieval calls.
    3. Re-checks conversation status transactionally with SELECT ... FOR UPDATE before saving any response.
    4. Drops AI replies immediately if conversation became staff_active or closed during generation.
    5. Any failure (including retrieval or provider exceptions) transitions safely to handoff without leaking errors.
    """
    conv_lock = await _get_conversation_lock(conversation_id)
    async with conv_lock:
        # Step 1: Pre-check conversation state without holding DB lock across remote I/O
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
        conv_res = await db.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()
        if not conv:
            return None

        if conv.status in (ConversationStatus.staff_active, ConversationStatus.closed):
            return None

        now = datetime.now(UTC)

        # Step 2: Triage Classification
        try:
            triage = await classify_message(
                db=db,
                conversation_id=conversation_id,
                current_message=visitor_message_content,
                llm_provider=llm_provider,
            )
        except Exception as exc:
            logger.warning(
                "Triage failed for conversation %s: %s; falling back to handoff",
                conversation_id,
                type(exc).__name__,
            )
            triage = TriageResult.fallback_handoff(reason="triage_failure")

        if not triage:
            return None

        # Step 3: Handle Handoff or Clarify from Triage
        if triage.action == TriageAction.handoff:
            recheck = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id).with_for_update()
            )
            active_conv = recheck.scalar_one_or_none()
            if not active_conv or active_conv.status in (ConversationStatus.staff_active, ConversationStatus.closed):
                await db.commit()
                return None

            handoff_reason = map_triage_to_handoff_reason(triage.reason)
            await create_or_reuse_handoff(
                db,
                conversation_id=conversation_id,
                reason=handoff_reason,
                summary=f"Triage handoff: {triage.reason}",
            )
            active_conv.status = ConversationStatus.waiting_for_staff
            active_conv.last_message_at = now
            active_conv.updated_at = now

            handoff_msg = Message(
                conversation_id=conversation_id,
                client_message_id=uuid.uuid4(),
                sender_type=SenderType.assistant,
                content=HANDOFF_MESSAGE_TEXT,
                metadata_json={"action": "handoff", "reason": triage.reason},
                created_at=now,
            )
            db.add(handoff_msg)
            await db.commit()
            return handoff_msg

        if triage.action == TriageAction.clarify:
            recheck = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id).with_for_update()
            )
            active_conv = recheck.scalar_one_or_none()
            if not active_conv or active_conv.status in (ConversationStatus.staff_active, ConversationStatus.closed):
                await db.commit()
                return None

            clarify_text = "Could you please clarify your request with a few more details so I can find the right information?"
            active_conv.last_message_at = now
            active_conv.updated_at = now

            clarify_msg = Message(
                conversation_id=conversation_id,
                client_message_id=uuid.uuid4(),
                sender_type=SenderType.assistant,
                content=clarify_text,
                metadata_json={"action": "clarify", "reason": triage.reason},
                created_at=now,
            )
            db.add(clarify_msg)
            await db.commit()
            return clarify_msg

        # Step 4: Evidence Retrieval for 'answer' (Protected with error handling)
        retrieval_failed = False
        retrieved_items: list[RetrievalResultItem] = []
        try:
            retrieval_res = await retrieve_knowledge_chunks(
                db=db,
                query=visitor_message_content,
                limit=5,
                min_score=settings.RETRIEVAL_MIN_SCORE,
            )
            retrieved_items = retrieval_res.results
        except Exception as exc:
            logger.warning(
                "Evidence retrieval failed for conversation %s: %s; falling back to handoff",
                conversation_id,
                type(exc).__name__,
            )
            retrieval_failed = True

        if retrieval_failed or not retrieved_items:
            recheck = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id).with_for_update()
            )
            active_conv = recheck.scalar_one_or_none()
            if not active_conv or active_conv.status in (ConversationStatus.staff_active, ConversationStatus.closed):
                await db.commit()
                return None

            summary_msg = (
                "Retrieval error occurred" if retrieval_failed else "No relevant published knowledge found"
            )
            await create_or_reuse_handoff(
                db,
                conversation_id=conversation_id,
                reason=HandoffReason.unsupported_request,
                summary=summary_msg,
            )
            active_conv.status = ConversationStatus.waiting_for_staff
            active_conv.last_message_at = now
            active_conv.updated_at = now

            handoff_msg = Message(
                conversation_id=conversation_id,
                client_message_id=uuid.uuid4(),
                sender_type=SenderType.assistant,
                content=HANDOFF_MESSAGE_TEXT,
                metadata_json={
                    "action": "handoff",
                    "reason": "retrieval_error" if retrieval_failed else "no_evidence_available",
                },
                created_at=now,
            )
            db.add(handoff_msg)
            await db.commit()
            return handoff_msg

        # Step 5: Answer Generation (Protected with error handling)
        try:
            answer_result = await generate_grounded_answer(
                db=db,
                conversation_id=conversation_id,
                visitor_query=visitor_message_content,
                retrieved_chunks=retrieved_items,
                llm_provider=llm_provider,
            )
        except Exception as exc:
            logger.warning(
                "Answer generation failed for conversation %s: %s; falling back to handoff",
                conversation_id,
                type(exc).__name__,
            )
            answer_result = GeneratorResult.fallback_handoff(reason="generation_failed")

        # Step 6: Final Transactional Gate
        recheck = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id).with_for_update()
        )
        active_conv = recheck.scalar_one_or_none()
        if not active_conv or active_conv.status in (ConversationStatus.staff_active, ConversationStatus.closed):
            logger.info(
                "Conversation %s transitioned to '%s' during generation; suppressing AI output.",
                conversation_id,
                active_conv.status.value if active_conv else "None",
            )
            await db.commit()
            return None

        if answer_result.action == GeneratorAction.handoff or not answer_result.answer:
            handoff_reason = (
                HandoffReason.low_confidence
                if answer_result.reason == "low_confidence"
                else HandoffReason.unsupported_request
            )
            await create_or_reuse_handoff(
                db,
                conversation_id=conversation_id,
                reason=handoff_reason,
                summary=f"Generation handoff: {answer_result.reason or 'unsupported'}",
            )
            active_conv.status = ConversationStatus.waiting_for_staff
            active_conv.last_message_at = now
            active_conv.updated_at = now

            handoff_msg = Message(
                conversation_id=conversation_id,
                client_message_id=uuid.uuid4(),
                sender_type=SenderType.assistant,
                content=HANDOFF_MESSAGE_TEXT,
                metadata_json={
                    "action": "handoff",
                    "reason": answer_result.reason or "generator_handoff",
                },
                created_at=now,
            )
            db.add(handoff_msg)
            await db.commit()
            return handoff_msg

        # Verified grounded answer!
        active_conv.last_message_at = now
        active_conv.updated_at = now

        sources_metadata = [
            {
                "chunk_id": str(item.chunk_id),
                "doc_id": str(item.document_id),
                "doc_title": item.document_title,
            }
            for item in retrieved_items
            if item.chunk_id in answer_result.cited_chunk_ids
        ]

        assistant_msg = Message(
            conversation_id=conversation_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.assistant,
            content=answer_result.answer,
            metadata_json={
                "action": "answer",
                "cited_chunk_ids": [str(cid) for cid in answer_result.cited_chunk_ids],
                "confidence": round(answer_result.confidence, 4),
                "sources": sources_metadata,
            },
            created_at=now,
        )
        db.add(assistant_msg)

        await record_audit(
            db,
            actor_type=ActorType.system,
            actor_id=None,
            event_type="assistant.answer",
            resource_type="message",
            resource_id=assistant_msg.id,
            metadata={
                "conversation_id": str(conversation_id),
                "cited_chunks_count": len(answer_result.cited_chunk_ids),
                "confidence": answer_result.confidence,
            },
        )
        await db.commit()
        await db.refresh(assistant_msg)
        return assistant_msg
