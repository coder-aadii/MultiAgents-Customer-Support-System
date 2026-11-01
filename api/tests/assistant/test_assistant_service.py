"""Tests for Phase 4 Sub-phase 4.3: Evidence retrieval, answer generation, and grounding."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.database import async_session_factory
from app.models.conversation import Conversation, ConversationStatus
from app.models.handoff import Handoff, HandoffReason, HandoffStatus
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import (
    DocumentStatus,
    IngestionStatus,
    KnowledgeDocument,
    SourceType,
)
from app.models.message import Message, SenderType
from app.models.user import User, UserRole
from app.models.visitor import Visitor
from app.providers import FakeEmbeddingProvider, FakeLLMProvider
from app.schemas.assistant import GeneratorAction
from app.schemas.retrieval import RetrievalResultItem
from app.security.password import hash_password
from app.services.assistant_service import (
    HANDOFF_MESSAGE_TEXT,
    generate_grounded_answer,
    handle_assistant_pipeline,
)


@pytest.fixture
async def setup_assistant_env():
    """Setup test user, published document with chunks, visitor, and conversation."""
    async with async_session_factory() as session:
        # User
        user_id = uuid.uuid4()
        user = User(
            id=user_id,
            email=f"asst_admin_{user_id.hex[:6]}@example.com",
            password_hash=hash_password("Pass123!"),
            display_name="Asst Admin",
            role=UserRole.admin,
            is_active=True,
        )
        session.add(user)

        # Published document
        doc_id = uuid.uuid4()
        doc = KnowledgeDocument(
            id=doc_id,
            title="MCSS Support Policies",
            source_type=SourceType.text,
            status=DocumentStatus.published,
            ingestion_status=IngestionStatus.ready,
            created_by_user_id=user_id,
            content="MCSS provides 24/7 technical assistance for enterprise clients.",
        )
        session.add(doc)

        # Chunks
        emb_provider = FakeEmbeddingProvider()
        chunk_content = "MCSS provides 24/7 technical assistance for enterprise clients."
        chunk_vec = await emb_provider.embed_text(chunk_content)

        chunk_id = uuid.uuid4()
        chunk = KnowledgeChunk(
            id=chunk_id,
            document_id=doc_id,
            ordinal=0,
            content=chunk_content,
            token_estimate=12,
            embedding=chunk_vec,
        )
        session.add(chunk)

        # Visitor and Conversation
        vis = Visitor(id=uuid.uuid4(), name="Asst Visitor", email="vis@example.com")
        session.add(vis)
        await session.flush()

        conv = Conversation(
            id=uuid.uuid4(),
            visitor_id=vis.id,
            status=ConversationStatus.open,
        )
        session.add(conv)
        await session.commit()

        return {
            "conversation_id": conv.id,
            "visitor_id": vis.id,
            "document_id": doc_id,
            "chunk_id": chunk_id,
            "chunk_content": chunk_content,
        }


def make_retrieval_item(
    chunk_id: uuid.UUID,
    doc_id: uuid.UUID,
    title: str = "MCSS Support Policies",
    content: str = "MCSS provides 24/7 technical assistance for enterprise clients.",
    score: float = 0.88,
) -> RetrievalResultItem:
    return RetrievalResultItem(
        chunk_id=chunk_id,
        document_id=doc_id,
        document_title=title,
        rank=1,
        score=score,
        content=content,
        token_estimate=len(content) // 4,
        start_char=0,
        end_char=len(content),
    )


@pytest.mark.asyncio
async def test_generate_grounded_answer_valid_citation(setup_assistant_env):
    """Ensure generator returns answer with validated chunk citations."""
    conv_id = setup_assistant_env["conversation_id"]
    chunk_id = setup_assistant_env["chunk_id"]

    retrieved = [
        make_retrieval_item(
            chunk_id=chunk_id,
            doc_id=setup_assistant_env["document_id"],
        )
    ]

    fake_provider = FakeLLMProvider()
    fake_provider.queue_response({
        "action": "answer",
        "answer": "MCSS provides 24/7 technical assistance to clients.",
        "cited_chunk_ids": [str(chunk_id)],
        "confidence": 0.95,
    })

    async with async_session_factory() as session:
        result = await generate_grounded_answer(
            db=session,
            conversation_id=conv_id,
            visitor_query="What are your support hours?",
            retrieved_chunks=retrieved,
            llm_provider=fake_provider,
        )

        assert result.action == GeneratorAction.answer
        assert result.answer == "MCSS provides 24/7 technical assistance to clients."
        assert result.cited_chunk_ids == [chunk_id]
        assert result.confidence == 0.95


@pytest.mark.asyncio
async def test_generate_grounded_answer_no_evidence_fallback(setup_assistant_env):
    """Ensure lack of evidence immediately triggers safe handoff without LLM call."""
    conv_id = setup_assistant_env["conversation_id"]
    fake_provider = FakeLLMProvider()

    async with async_session_factory() as session:
        result = await generate_grounded_answer(
            db=session,
            conversation_id=conv_id,
            visitor_query="Question with no sources",
            retrieved_chunks=[],
            llm_provider=fake_provider,
        )

        assert result.action == GeneratorAction.handoff
        assert result.reason == "no_evidence_available"
        assert fake_provider.call_count == 0


@pytest.mark.asyncio
async def test_generate_grounded_answer_rejects_fabricated_citation(setup_assistant_env):
    """Ensure fabricated citation not in retrieved chunks forces safe handoff."""
    conv_id = setup_assistant_env["conversation_id"]
    chunk_id = setup_assistant_env["chunk_id"]
    fake_chunk_id = uuid.uuid4()

    retrieved = [
        make_retrieval_item(
            chunk_id=chunk_id,
            doc_id=setup_assistant_env["document_id"],
        )
    ]

    fake_provider = FakeLLMProvider()
    fake_provider.queue_response({
        "action": "answer",
        "answer": "Hallucinated citation answer.",
        "cited_chunk_ids": [str(fake_chunk_id)],
        "confidence": 0.95,
    })

    async with async_session_factory() as session:
        result = await generate_grounded_answer(
            db=session,
            conversation_id=conv_id,
            visitor_query="Query",
            retrieved_chunks=retrieved,
            llm_provider=fake_provider,
        )

        assert result.action == GeneratorAction.handoff
        assert result.reason == "fabricated_citation"


@pytest.mark.asyncio
async def test_generate_grounded_answer_rejects_duplicate_citations(setup_assistant_env):
    """Ensure duplicate citations trigger safe handoff."""
    conv_id = setup_assistant_env["conversation_id"]
    chunk_id = setup_assistant_env["chunk_id"]

    retrieved = [
        make_retrieval_item(
            chunk_id=chunk_id,
            doc_id=setup_assistant_env["document_id"],
        )
    ]

    fake_provider = FakeLLMProvider()
    fake_provider.queue_response({
        "action": "answer",
        "answer": "Duplicate citation answer.",
        "cited_chunk_ids": [str(chunk_id), str(chunk_id)],
        "confidence": 0.95,
    })

    async with async_session_factory() as session:
        result = await generate_grounded_answer(
            db=session,
            conversation_id=conv_id,
            visitor_query="Query",
            retrieved_chunks=retrieved,
            llm_provider=fake_provider,
        )

        assert result.action == GeneratorAction.handoff
        assert result.reason == "duplicate_citations"


@pytest.mark.asyncio
async def test_generate_grounded_answer_low_confidence_fallback(setup_assistant_env):
    """Ensure self-reported confidence < 0.75 triggers safe handoff."""
    conv_id = setup_assistant_env["conversation_id"]
    chunk_id = setup_assistant_env["chunk_id"]

    retrieved = [
        make_retrieval_item(
            chunk_id=chunk_id,
            doc_id=setup_assistant_env["document_id"],
        )
    ]

    fake_provider = FakeLLMProvider()
    fake_provider.queue_response({
        "action": "answer",
        "answer": "Low confidence answer.",
        "cited_chunk_ids": [str(chunk_id)],
        "confidence": 0.60,
    })

    async with async_session_factory() as session:
        result = await generate_grounded_answer(
            db=session,
            conversation_id=conv_id,
            visitor_query="Query",
            retrieved_chunks=retrieved,
            llm_provider=fake_provider,
        )

        assert result.action == GeneratorAction.handoff
        assert result.reason == "low_confidence"


@pytest.mark.asyncio
async def test_generate_grounded_answer_schema_repair_retry(setup_assistant_env):
    """Ensure exactly one repair attempt is made on schema-invalid response."""
    conv_id = setup_assistant_env["conversation_id"]
    chunk_id = setup_assistant_env["chunk_id"]

    retrieved = [
        make_retrieval_item(
            chunk_id=chunk_id,
            doc_id=setup_assistant_env["document_id"],
        )
    ]

    fake_provider = FakeLLMProvider()
    # 1st attempt: invalid raw text
    fake_provider.queue_response("Invalid raw non-json text")
    # 2nd attempt: valid response
    fake_provider.queue_response({
        "action": "answer",
        "answer": "Repaired valid answer.",
        "cited_chunk_ids": [str(chunk_id)],
        "confidence": 0.90,
    })

    async with async_session_factory() as session:
        result = await generate_grounded_answer(
            db=session,
            conversation_id=conv_id,
            visitor_query="Query",
            retrieved_chunks=retrieved,
            llm_provider=fake_provider,
        )

        assert result.action == GeneratorAction.answer
        assert result.answer == "Repaired valid answer."
        assert fake_provider.call_count == 2


@pytest.mark.asyncio
async def test_generate_grounded_answer_rejects_html_tags(setup_assistant_env):
    """Ensure answers with HTML tags are rejected by schema and trigger handoff."""
    conv_id = setup_assistant_env["conversation_id"]
    chunk_id = setup_assistant_env["chunk_id"]

    retrieved = [
        make_retrieval_item(
            chunk_id=chunk_id,
            doc_id=setup_assistant_env["document_id"],
        )
    ]

    fake_provider = FakeLLMProvider()
    # Both attempts return HTML
    fake_provider.queue_response({
        "action": "answer",
        "answer": "<b>Injected HTML</b><script>alert(1)</script>",
        "cited_chunk_ids": [str(chunk_id)],
        "confidence": 0.95,
    })
    fake_provider.queue_response({
        "action": "answer",
        "answer": "<i>Still HTML</i>",
        "cited_chunk_ids": [str(chunk_id)],
        "confidence": 0.95,
    })

    async with async_session_factory() as session:
        result = await generate_grounded_answer(
            db=session,
            conversation_id=conv_id,
            visitor_query="Query",
            retrieved_chunks=retrieved,
            llm_provider=fake_provider,
        )

        assert result.action == GeneratorAction.handoff
        assert result.reason == "schema_verification_failed"


# --- Full Assistant Pipeline Integration Tests ---


@pytest.mark.asyncio
async def test_full_pipeline_answers_faq(setup_assistant_env):
    """End-to-end pipeline: Visitor FAQ query -> Answer with citations generated and persisted."""
    conv_id = setup_assistant_env["conversation_id"]
    chunk_id = setup_assistant_env["chunk_id"]
    fake_provider = FakeLLMProvider()

    # Triage returns 'answer'
    fake_provider.queue_response({"action": "answer", "reason": "faq_match"})

    async with async_session_factory() as session:
        msg = await handle_assistant_pipeline(
            db=session,
            conversation_id=conv_id,
            visitor_message_content=setup_assistant_env["chunk_content"],
            llm_provider=fake_provider,
        )

        assert msg is not None
        assert msg.sender_type == SenderType.assistant
        assert msg.metadata_json["action"] == "answer"
        assert len(msg.metadata_json["cited_chunk_ids"]) >= 1
        assert len(msg.metadata_json["sources"]) >= 1


@pytest.mark.asyncio
async def test_full_pipeline_handoff_human_request(setup_assistant_env):
    """End-to-end pipeline: Human request -> Triage triggers handoff -> waiting_for_staff state."""
    conv_id = setup_assistant_env["conversation_id"]
    fake_provider = FakeLLMProvider()

    # Triage returns handoff
    fake_provider.queue_response({"action": "handoff", "reason": "human_request"})

    async with async_session_factory() as session:
        msg = await handle_assistant_pipeline(
            db=session,
            conversation_id=conv_id,
            visitor_message_content="I want to speak to a person right now",
            llm_provider=fake_provider,
        )

        assert msg is not None
        assert msg.sender_type == SenderType.assistant
        assert msg.content == HANDOFF_MESSAGE_TEXT
        assert msg.metadata_json["action"] == "handoff"

        # Verify conversation status changed to waiting_for_staff
        conv = await session.get(Conversation, conv_id)
        assert conv.status == ConversationStatus.waiting_for_staff

        # Verify handoff record created
        handoff_res = await session.execute(
            select(Handoff).where(Handoff.conversation_id == conv_id)
        )
        handoff = handoff_res.scalar_one_or_none()
        assert handoff is not None
        assert handoff.status == HandoffStatus.queued
        assert handoff.reason == HandoffReason.visitor_requested


@pytest.mark.asyncio
async def test_full_pipeline_discards_ai_reply_if_staff_active(setup_assistant_env):
    """Invariant: If conversation becomes staff_active during processing, AI reply is discarded."""
    conv_id = setup_assistant_env["conversation_id"]
    fake_provider = FakeLLMProvider()

    # Mark conversation staff_active
    async with async_session_factory() as session:
        conv = await session.get(Conversation, conv_id)
        conv.status = ConversationStatus.staff_active
        await session.commit()

    async with async_session_factory() as session:
        msg = await handle_assistant_pipeline(
            db=session,
            conversation_id=conv_id,
            visitor_message_content="Hello?",
            llm_provider=fake_provider,
        )

        # Discarded: no message returned or persisted
        assert msg is None
        assert fake_provider.call_count == 0


@pytest.mark.asyncio
async def test_generate_grounded_answer_preserves_math_comparisons(setup_assistant_env):
    """Invariant: Math comparisons (e.g. < and >) are preserved and not rejected as HTML."""
    conv_id = setup_assistant_env["conversation_id"]
    chunk_id = setup_assistant_env["chunk_id"]
    doc_id = setup_assistant_env["document_id"]

    retrieved = [
        RetrievalResultItem(
            chunk_id=chunk_id,
            document_id=doc_id,
            document_title="Pricing Guidelines",
            rank=1,
            score=0.95,
            content="Weights < 5kg and dimensions > 20cm are standard tier.",
            token_estimate=15,
            start_char=0,
            end_char=55,
        )
    ]

    fake_provider = FakeLLMProvider()
    fake_provider.queue_response({
        "action": "answer",
        "answer": "Tier 1 covers items where weight < 5kg and width > 10cm.",
        "cited_chunk_ids": [str(chunk_id)],
        "confidence": 0.95,
    })

    async with async_session_factory() as session:
        result = await generate_grounded_answer(
            db=session,
            conversation_id=conv_id,
            visitor_query="What are the dimensions?",
            retrieved_chunks=retrieved,
            llm_provider=fake_provider,
        )

        assert result.action == GeneratorAction.answer
        assert "< 5kg" in result.answer
        assert "> 10cm" in result.answer
        assert result.cited_chunk_ids == [chunk_id]


@pytest.mark.asyncio
async def test_full_pipeline_handles_retrieval_failure_with_safe_handoff(setup_assistant_env, monkeypatch):
    """Invariant: Provider or DB failure during retrieval gracefully triggers handoff."""
    conv_id = setup_assistant_env["conversation_id"]
    fake_provider = FakeLLMProvider()

    # Monkeypatch retrieve_knowledge_chunks to simulate timeout/vector error
    async def mock_failing_retrieval(*args, **kwargs):
        raise ProviderTimeoutError("Vector retrieval connection timed out")

    monkeypatch.setattr(
        "app.services.assistant_service.retrieve_knowledge_chunks",
        mock_failing_retrieval,
    )

    async with async_session_factory() as session:
        msg = await handle_assistant_pipeline(
            db=session,
            conversation_id=conv_id,
            visitor_message_content="Where is the documentation?",
            llm_provider=fake_provider,
        )

        # Message is safe handoff
        assert msg is not None
        assert msg.sender_type == SenderType.assistant
        assert msg.content == HANDOFF_MESSAGE_TEXT
        assert msg.metadata_json["action"] == "handoff"
        assert msg.metadata_json["reason"] == "retrieval_error"

        # Conversation status transitioned to waiting_for_staff
        conv = await session.get(Conversation, conv_id)
        assert conv.status == ConversationStatus.waiting_for_staff

        # Handoff record created
        handoff_res = await session.execute(
            select(Handoff).where(Handoff.conversation_id == conv_id)
        )
        handoff = handoff_res.scalar_one_or_none()
        assert handoff is not None
        assert handoff.status == HandoffStatus.queued

