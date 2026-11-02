"""Tests for Sub-phase 5A.2: Typed Evidence References & Schema Generalization."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.common.errors import NotFoundError
from app.database import async_session_factory
from app.models.conversation import Conversation, ConversationStatus
from app.models.evidence_record import EvidenceRecord
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import (
    DocumentStatus,
    IngestionStatus,
    KnowledgeDocument,
    SourceType,
)
from app.models.user import User, UserRole
from app.models.visitor import Visitor
from app.schemas.evidence import (
    EvidenceRecordCreate,
    EvidenceReference,
    EvidenceType,
    TurnEvidenceRegistry,
    VerifiedFact,
)
from app.schemas.message import MessageResponse
from app.services.evidence_service import (
    get_evidence_record,
    list_conversation_evidence,
    purge_expired_evidence,
    record_evidence,
    to_public_evidence_reference,
)


@pytest.fixture
async def setup_conversation():
    """Create a test visitor and conversation."""
    async with async_session_factory() as db:
        now = datetime.now(UTC)
        visitor = Visitor(name="Evidence Test Visitor", email="evidence@test.com", created_at=now, last_seen_at=now)
        db.add(visitor)
        await db.flush()

        conv = Conversation(
            visitor_id=visitor.id,
            status=ConversationStatus.open,
            created_at=now,
            updated_at=now,
        )
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        return conv.id, visitor.id


@pytest.fixture
async def setup_two_conversations(setup_conversation):
    """Create two conversations for cross-isolation testing."""
    conv1_id, visitor_id = setup_conversation
    async with async_session_factory() as db:
        now = datetime.now(UTC)
        conv2 = Conversation(
            visitor_id=visitor_id,
            status=ConversationStatus.open,
            created_at=now,
            updated_at=now,
        )
        db.add(conv2)
        await db.commit()
        await db.refresh(conv2)
        return conv1_id, conv2.id


def test_evidence_reference_serialization_excludes_private_fields():
    """Verify EvidenceReference public schema never serializes private data or internal fields."""
    ev_id = uuid.uuid4()
    record = EvidenceRecord(
        id=ev_id,
        conversation_id=uuid.uuid4(),
        evidence_key="order:ORD-1024",
        source_type="business_entity",
        title="Order #ORD-1024",
        entity_type="order",
        payload_json={
            "customer_ssn": "123-45-6789",
            "internal_margin": 45.2,
            "private_address": "742 Evergreen Terrace",
            "access_token": "super-secret-token",
        },
        verified_customer_id="cust_private_123",
    )

    public_ref = to_public_evidence_reference(record)
    serialized = public_ref.model_dump()

    # Public fields present
    assert serialized["evidence_id"] == f"ref_{ev_id}"
    assert serialized["source_type"] == EvidenceType.business_entity
    assert serialized["title"] == "Order #ORD-1024"
    assert serialized["entity_type"] == "order"

    # Private fields STRICTLY absent
    serialized_str = public_ref.model_dump_json()
    assert "customer_ssn" not in serialized_str
    assert "123-45-6789" not in serialized_str
    assert "internal_margin" not in serialized_str
    assert "private_address" not in serialized_str
    assert "access_token" not in serialized_str
    assert "cust_private_123" not in serialized_str
    assert "payload_json" not in serialized_str


def test_backward_compatibility_retains_cited_chunk_ids():
    """Verify MessageResponse and metadata preserve cited_chunk_ids and chunk_id on citations."""
    chunk_uuid = uuid.uuid4()
    doc_uuid = uuid.uuid4()

    # Knowledge chunk reference
    ev_ref = EvidenceReference(
        evidence_id=f"ref_{chunk_uuid}",
        source_type=EvidenceType.knowledge_chunk,
        title="Return & Refund Policy",
        chunk_id=str(chunk_uuid),
        doc_id=str(doc_uuid),
    )

    msg_data = {
        "id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "client_message_id": uuid.uuid4(),
        "sender_type": "assistant",
        "sender_user_id": None,
        "content": "You can return items within 30 days.",
        "metadata_json": {
            "action": "answer",
            "cited_chunk_ids": [str(chunk_uuid)],
            "confidence": 0.95,
            "sources": [ev_ref.model_dump()],
        },
        "created_at": datetime.now(UTC),
    }

    resp = MessageResponse.model_validate(msg_data)
    assert resp.metadata["cited_chunk_ids"] == [str(chunk_uuid)]
    assert len(resp.metadata["sources"]) == 1
    src = resp.metadata["sources"][0]
    # UI lookup: src.chunk_id === chunkId
    assert src["chunk_id"] == str(chunk_uuid)
    assert src["doc_id"] == str(doc_uuid)
    assert src["evidence_id"] == f"ref_{chunk_uuid}"


@pytest.mark.asyncio
async def test_evidence_record_database_constraints(setup_conversation):
    """Verify unique constraint on (conversation_id, evidence_key) and foreign key cascade."""
    conv_id, _ = setup_conversation

    async with async_session_factory() as db:
        # 1. Insert record
        r1 = await record_evidence(
            db=db,
            conversation_id=conv_id,
            evidence_key="order:ORD-9999",
            source_type=EvidenceType.business_entity,
            title="Order #ORD-9999",
            payload_json={"status": "in_transit"},
            entity_type="order",
        )
        assert r1.id is not None
        await db.commit()

        # 2. Re-recording with same key updates existing record (upsert)
        r2 = await record_evidence(
            db=db,
            conversation_id=conv_id,
            evidence_key="order:ORD-9999",
            source_type=EvidenceType.business_entity,
            title="Order #ORD-9999 Updated",
            payload_json={"status": "delivered"},
            entity_type="order",
        )
        assert r2.id == r1.id
        assert r2.title == "Order #ORD-9999 Updated"
        await db.commit()

    # 3. Test CASCADE on conversation deletion
    async with async_session_factory() as db:
        c = await db.get(Conversation, conv_id)
        assert c is not None
        await db.delete(c)
        await db.commit()

        # Evidence record should be automatically cascaded
        ev_stmt = select(EvidenceRecord).where(EvidenceRecord.conversation_id == conv_id)
        res = await db.execute(ev_stmt)
        assert res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_evidence_ownership_and_cross_conversation_isolation(setup_two_conversations):
    """Verify conversation A cannot access conversation B's evidence records."""
    conv1_id, conv2_id = setup_two_conversations

    async with async_session_factory() as db:
        rec1 = await record_evidence(
            db=db,
            conversation_id=conv1_id,
            evidence_key="order:ORD-ISOLATE",
            source_type=EvidenceType.business_entity,
            title="Order #ORD-ISOLATE",
            payload_json={"secret": "conv1_secret"},
            verified_customer_id="cust_alice",
        )
        await db.commit()
        ev_id = rec1.id

    # 1. Accessing with right conversation and customer -> OK
    async with async_session_factory() as db:
        found = await get_evidence_record(
            db=db,
            evidence_id=ev_id,
            conversation_id=conv1_id,
            verified_customer_id="cust_alice",
        )
        assert found.id == ev_id
        assert found.payload_json["secret"] == "conv1_secret"

    # 2. Accessing from wrong conversation -> 404 NotFoundError
    async with async_session_factory() as db:
        with pytest.raises(NotFoundError):
            await get_evidence_record(
                db=db,
                evidence_id=ev_id,
                conversation_id=conv2_id,
                verified_customer_id="cust_alice",
            )

    # 3. Accessing from same conversation but wrong customer -> 404 NotFoundError
    async with async_session_factory() as db:
        with pytest.raises(NotFoundError):
            await get_evidence_record(
                db=db,
                evidence_id=ev_id,
                conversation_id=conv1_id,
                verified_customer_id="cust_bob",
            )

    # 4. Accessing from same conversation with anonymous context (None) -> 404 NotFoundError
    async with async_session_factory() as db:
        with pytest.raises(NotFoundError):
            await get_evidence_record(
                db=db,
                evidence_id=ev_id,
                conversation_id=conv1_id,
                verified_customer_id=None,
            )


@pytest.mark.asyncio
async def test_invalid_evidence_ids_rejected(setup_conversation):
    """Verify malformed or non-existent evidence IDs raise NotFoundError."""
    conv_id, _ = setup_conversation

    async with async_session_factory() as db:
        # Malformed ID
        with pytest.raises(NotFoundError):
            await get_evidence_record(db=db, evidence_id="invalid-uuid-format", conversation_id=conv_id)

        # Non-existent valid UUID
        with pytest.raises(NotFoundError):
            await get_evidence_record(db=db, evidence_id=uuid.uuid4(), conversation_id=conv_id)


@pytest.mark.asyncio
async def test_evidence_expiration_and_retention(setup_conversation):
    """Verify expired evidence records return 404 and are cleaned up by purge."""
    conv_id, _ = setup_conversation

    async with async_session_factory() as db:
        # Create already expired record
        rec = await record_evidence(
            db=db,
            conversation_id=conv_id,
            evidence_key="order:EXPIRED",
            source_type=EvidenceType.business_entity,
            title="Expired Order",
            payload_json={"status": "old"},
            expires_in_days=-1,  # expired yesterday
        )
        await db.commit()
        ev_id = rec.id

    async with async_session_factory() as db:
        # 1. get_evidence_record returns NotFoundError for expired record
        with pytest.raises(NotFoundError):
            await get_evidence_record(db=db, evidence_id=ev_id, conversation_id=conv_id)

        # 2. list_conversation_evidence filters out expired record
        active_list = await list_conversation_evidence(db=db, conversation_id=conv_id)
        assert all(r.id != ev_id for r in active_list)

        # 3. purge_expired_evidence deletes it
        purged = await purge_expired_evidence(db)
        assert purged >= 1

        # Confirm gone from DB
        check = await db.get(EvidenceRecord, ev_id)
        assert check is None


@pytest.mark.asyncio
async def test_archived_knowledge_sources(setup_conversation):
    """Verify archived knowledge documents preserve historical evidence records."""
    conv_id, _ = setup_conversation
    now = datetime.now(UTC)

    async with async_session_factory() as db:
        # Find or create user for created_by_user_id
        user_res = await db.execute(select(User).limit(1))
        user = user_res.scalar_one_or_none()
        if not user:
            user = User(
                email=f"author_{uuid.uuid4().hex[:6]}@example.com",
                password_hash="fakehash",
                display_name="Policy Author",
                role=UserRole.admin,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
            await db.flush()

        # Create knowledge document and chunk
        doc = KnowledgeDocument(
            title="Legacy Warranty Policy",
            source_type=SourceType.markdown,
            content="Items under 1 year covered.",
            status=DocumentStatus.published,
            ingestion_status=IngestionStatus.ready,
            created_by_user_id=user.id,
            created_at=now,
            updated_at=now,
        )
        db.add(doc)
        await db.flush()

        chunk = KnowledgeChunk(
            document_id=doc.id,
            ordinal=0,
            content="Items under 1 year covered.",
            token_estimate=6,
            embedding=[0.0] * 1536,
            created_at=now,
        )
        db.add(chunk)
        await db.flush()

        # Record evidence referencing this published chunk
        ev_rec = await record_evidence(
            db=db,
            conversation_id=conv_id,
            evidence_key=f"chunk:{chunk.id}",
            source_type=EvidenceType.knowledge_chunk,
            title=doc.title,
            payload_json={"chunk_id": str(chunk.id), "doc_id": str(doc.id), "content": chunk.content},
        )
        await db.commit()
        ev_id = ev_rec.id
        doc_id = doc.id

    # Now archive the document
    async with async_session_factory() as db:
        doc_to_archive = await db.get(KnowledgeDocument, doc_id)
        assert doc_to_archive is not None
        doc_to_archive.status = DocumentStatus.archived
        await db.commit()

    # Historical conversation evidence MUST remain intact and readable for staff audit
    async with async_session_factory() as db:
        historical_ev = await get_evidence_record(db=db, evidence_id=ev_id, conversation_id=conv_id)
        assert historical_ev.id == ev_id
        assert historical_ev.title == "Legacy Warranty Policy"
        assert historical_ev.payload_json["content"] == "Items under 1 year covered."


def test_turn_evidence_registry_and_verified_fact():
    """Verify TurnEvidenceRegistry captures records and facts during turn execution."""
    conv_id = uuid.uuid4()
    registry = TurnEvidenceRegistry(conversation_id=conv_id)

    record_create = EvidenceRecordCreate(
        evidence_key="order:ORD-5555",
        source_type=EvidenceType.business_entity,
        title="Order #ORD-5555",
        entity_type="order",
        payload_json={"order_id": "ORD-5555", "status": "shipped", "carrier": "FedEx"},
    )
    registry.register_record(record_create)

    fact = VerifiedFact(
        entity_type="order",
        entity_id="ORD-5555",
        field_name="status",
        field_value="shipped",
        source_evidence_id="ref_5555",
    )
    registry.register_fact(fact)

    assert len(registry.records) == 1
    assert registry.records[0].evidence_key == "order:ORD-5555"
    assert len(registry.facts) == 1
    assert registry.facts[0].field_value == "shipped"
    assert registry.facts[0].entity_id == "ORD-5555"
