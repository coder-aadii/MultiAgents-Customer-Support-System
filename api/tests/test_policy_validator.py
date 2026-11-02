from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.database import async_session_factory
from app.gateway.base import EntityRecord
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import SenderType
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.providers.fake_provider import FakeLLMProvider
from app.schemas.assistant import TriageAction
from app.schemas.evidence import VerifiedFact
from app.services.assistant_service import handle_assistant_pipeline
from app.services.policy_validator import PolicyValidator, validate_response_grounding
from app.services.template_engine import TemplateEngine
from app.services.triage_service import classify_message


def test_deterministic_template_bypasses_llm_for_status() -> None:
    """Verify deterministic templates render exact, un-hallucinated status responses."""
    # Order template
    order = EntityRecord(
        entity_type="order",
        entity_id="ORD-1001",
        customer_id="cust_test_1",
        title="Order ORD-1001",
        summary_fields={
            "status": "delivered",
            "delivered_at": "2026-10-25",
            "carrier": "FedFast",
            "tracking_number": "TRK-8801",
            "total_amount": 149.50,
            "currency": "USD",
        },
    )
    rendered = TemplateEngine.render_entity_status(order)
    assert rendered == "Order ORD-1001 was delivered on 2026-10-25 via FedFast (Tracking: TRK-8801)."

    # In-transit order template
    in_transit_order = EntityRecord(
        entity_type="order",
        entity_id="ORD-1002",
        customer_id="cust_test_1",
        title="Order ORD-1002",
        summary_fields={
            "status": "in_transit",
            "carrier": "DHL",
            "estimated_delivery": "2026-11-04",
            "tracking_number": "TRK-8802",
        },
    )
    rendered_transit = TemplateEngine.render_entity_status(in_transit_order)
    assert "in transit with DHL" in rendered_transit
    assert "2026-11-04" in rendered_transit

    # Shipment template
    shipment = EntityRecord(
        entity_type="shipment",
        entity_id="TRK-8801",
        customer_id="cust_test_1",
        title="Shipment TRK-8801",
        summary_fields={
            "status": "delivered",
            "order_id": "ORD-1001",
            "carrier": "FedFast",
            "delivered_at": "2026-10-25",
        },
    )
    rendered_shipment = TemplateEngine.render_entity_status(shipment)
    assert "Shipment TRK-8801 for order ORD-1001 was delivered on 2026-10-25 via FedFast." == rendered_shipment

    # Refund template
    refund = EntityRecord(
        entity_type="refund",
        entity_id="REF-5001",
        customer_id="cust_test_1",
        title="Refund REF-5001",
        summary_fields={
            "status": "completed",
            "order_id": "ORD-1001",
            "amount": 25.00,
            "currency": "USD",
        },
    )
    rendered_refund = TemplateEngine.render_entity_status(refund)
    assert rendered_refund == "Refund REF-5001 for order ORD-1001 in the amount of USD 25.00 is currently completed."

    # List template
    rendered_list = TemplateEngine.render_entity_list("order", [order, in_transit_order])
    assert "You have 2 recent order(s):" in rendered_list
    assert "- Order ORD-1001 (delivered, USD 149.50)" in rendered_list


def test_policy_validator_rejects_hallucinated_amounts() -> None:
    """Verify PolicyValidator catches and rejects ungrounded monetary amounts."""
    facts = [
        VerifiedFact(
            entity_type="order",
            entity_id="ORD-1001",
            field_name="total_amount",
            field_value=49.99,
            source_evidence_id="ref_test",
        ),
        VerifiedFact(
            entity_type="order",
            entity_id="ORD-1001",
            field_name="id",
            field_value="ORD-1001",
            source_evidence_id="ref_test",
        ),
    ]

    # Valid grounded amount
    valid_text = "Your order ORD-1001 total is $49.99."
    is_valid, reason = PolicyValidator.validate_response_grounding(valid_text, facts)
    assert is_valid is True
    assert reason is None

    # Hallucinated amount
    hallucinated_text = "Your order ORD-1001 total is $120.00."
    is_valid, reason = PolicyValidator.validate_response_grounding(hallucinated_text, facts)
    assert is_valid is False
    assert "Ungrounded monetary amount: $120.00" in (reason or "")


def test_policy_validator_rejects_unverified_dates() -> None:
    """Verify PolicyValidator catches and rejects ungrounded ISO dates."""
    facts = [
        VerifiedFact(
            entity_type="order",
            entity_id="ORD-1001",
            field_name="delivered_at",
            field_value="2026-10-25",
            source_evidence_id="ref_test",
        ),
        VerifiedFact(
            entity_type="order",
            entity_id="ORD-1001",
            field_name="id",
            field_value="ORD-1001",
            source_evidence_id="ref_test",
        ),
    ]

    # Grounded date
    valid_text = "Order ORD-1001 was delivered on 2026-10-25."
    assert validate_response_grounding(valid_text, facts) is True

    # Hallucinated date
    fake_date_text = "Order ORD-1001 will arrive on 2026-11-20."
    is_valid, reason = PolicyValidator.validate_response_grounding(fake_date_text, facts)
    assert is_valid is False
    assert "Ungrounded date mentioned: '2026-11-20'" in (reason or "")


def test_policy_validator_rejects_ungrounded_entity_ids() -> None:
    """Verify PolicyValidator rejects ungrounded entity IDs."""
    facts = [
        VerifiedFact(
            entity_type="order",
            entity_id="ORD-1001",
            field_name="id",
            field_value="ORD-1001",
            source_evidence_id="ref_test",
        ),
    ]

    # Valid entity ID
    assert validate_response_grounding("Order ORD-1001 is confirmed.", facts) is True

    # Hallucinated entity ID
    is_valid, reason = PolicyValidator.validate_response_grounding("Order ORD-9999 is confirmed.", facts)
    assert is_valid is False
    assert "Ungrounded entity ID mentioned: 'ORD-9999'" in (reason or "")


@pytest.mark.asyncio
async def test_triage_routes_verified_customer_to_business_data() -> None:
    """Verified customer (IAL >= 2) requesting order information routes to business_data."""
    async with async_session_factory() as db_session:
        visitor = Visitor(id=uuid.uuid4(), name="Verified Alice")
        db_session.add(visitor)
        await db_session.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db_session.add(conv)
        await db_session.flush()

        session = VisitorSession(
            id=uuid.uuid4(),
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"hash_verified_test_{uuid.uuid4()}",
            origin="http://localhost:5174",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            verified_customer_id="cust_test_1",
            identity_assurance_level=2,
            customer_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db_session.add(session)
        await db_session.commit()

        fake_provider = FakeLLMProvider()
        fake_provider.queue_response({"action": "business_data", "reason": "order_lookup"})

        result = await classify_message(
            db=db_session,
            conversation_id=conv.id,
            current_message="Where is my order ORD-1001?",
            llm_provider=fake_provider,
        )

        assert result is not None
        assert result.action == TriageAction.business_data
        assert result.reason == "order_lookup"


@pytest.mark.asyncio
async def test_triage_prompts_unverified_visitor_to_authenticate() -> None:
    """Unverified visitor (IAL < 2) requesting business data is routed to clarify with authentication_required."""
    async with async_session_factory() as db_session:
        visitor = Visitor(id=uuid.uuid4(), name="Anonymous Bob")
        db_session.add(visitor)
        await db_session.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db_session.add(conv)
        await db_session.flush()

        session = VisitorSession(
            id=uuid.uuid4(),
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"hash_anon_test_{uuid.uuid4()}",
            origin="http://localhost:5174",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            identity_assurance_level=0,
        )
        db_session.add(session)
        await db_session.commit()

        fake_provider = FakeLLMProvider()
        fake_provider.queue_response({"action": "business_data", "reason": "order_lookup"})

        result = await classify_message(
            db=db_session,
            conversation_id=conv.id,
            current_message="Can you look up order ORD-1001 for me?",
            llm_provider=fake_provider,
        )

        assert result is not None
        assert result.action == TriageAction.clarify
        assert result.reason == "authentication_required"


@pytest.mark.asyncio
async def test_handle_assistant_pipeline_verified_business_data() -> None:
    """Full pipeline test: Verified customer receives deterministic templated status and opaque evidence reference."""
    async with async_session_factory() as db_session:
        visitor = Visitor(id=uuid.uuid4(), name="Verified Alice")
        db_session.add(visitor)
        await db_session.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db_session.add(conv)
        await db_session.flush()

        session = VisitorSession(
            id=uuid.uuid4(),
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"hash_full_verified_test_{uuid.uuid4()}",
            origin="http://localhost:5174",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            verified_customer_id="cust_verified_123",
            identity_assurance_level=2,
            customer_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db_session.add(session)
        await db_session.commit()

        fake_provider = FakeLLMProvider()
        fake_provider.queue_response({"action": "business_data", "reason": "order_lookup"})

        msg = await handle_assistant_pipeline(
            db=db_session,
            conversation_id=conv.id,
            visitor_message_content="Where is my order ORD-1001?",
            llm_provider=fake_provider,
        )

        assert msg is not None
        assert msg.sender_type == SenderType.assistant
        assert "Order ORD-1001 was delivered on 2026-10-25 via FastShip Express" in msg.content
        assert msg.metadata_json is not None
        assert msg.metadata_json.get("action") == "answer"
        assert msg.metadata_json.get("confidence") == 1.0

        sources = msg.metadata_json.get("sources", [])
        assert len(sources) == 1
        evidence_ref = sources[0]
        assert evidence_ref["evidence_id"].startswith("ref_")
        assert evidence_ref["entity_type"] == "order"
        assert "cust_verified_123" not in str(evidence_ref)  # Never expose customer ID in public reference


@pytest.mark.asyncio
async def test_handle_assistant_pipeline_cross_customer_isolation() -> None:
    """Cross-customer isolation: Customer A cannot access Customer B's orders (BOLA prevention)."""
    async with async_session_factory() as db_session:
        visitor = Visitor(id=uuid.uuid4(), name="Other Customer")
        db_session.add(visitor)
        await db_session.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db_session.add(conv)
        await db_session.flush()

        session = VisitorSession(
            id=uuid.uuid4(),
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"hash_cross_cust_{uuid.uuid4()}",
            origin="http://localhost:5174",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            verified_customer_id="cust_other_999",
            identity_assurance_level=2,
            customer_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db_session.add(session)
        await db_session.commit()

        fake_provider = FakeLLMProvider()
        fake_provider.queue_response({"action": "business_data", "reason": "order_lookup"})

        msg = await handle_assistant_pipeline(
            db=db_session,
            conversation_id=conv.id,
            visitor_message_content="Where is order ORD-1001?",
            llm_provider=fake_provider,
        )

        assert msg is not None
        assert msg.sender_type == SenderType.assistant
        assert "I could not find record ORD-1001 in your account" in msg.content
        assert msg.metadata_json.get("action") == "answer"
        assert msg.metadata_json.get("reason") == "entity_not_found"


@pytest.mark.asyncio
async def test_handle_assistant_pipeline_unverified_prompt_to_authenticate() -> None:
    """Full pipeline test: Unverified visitor requesting business data receives login prompt."""
    async with async_session_factory() as db_session:
        visitor = Visitor(id=uuid.uuid4(), name="Anonymous Bob")
        db_session.add(visitor)
        await db_session.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db_session.add(conv)
        await db_session.flush()

        session = VisitorSession(
            id=uuid.uuid4(),
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"hash_full_anon_test_{uuid.uuid4()}",
            origin="http://localhost:5174",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            identity_assurance_level=0,
        )
        db_session.add(session)
        await db_session.commit()

        fake_provider = FakeLLMProvider()
        fake_provider.queue_response({"action": "business_data", "reason": "order_lookup"})

        msg = await handle_assistant_pipeline(
            db=db_session,
            conversation_id=conv.id,
            visitor_message_content="Where is my order ORD-1001?",
            llm_provider=fake_provider,
        )

        assert msg is not None
        assert msg.sender_type == SenderType.assistant
        assert "Please log in to your store account" in msg.content
        assert msg.metadata_json is not None
        assert msg.metadata_json.get("action") == "clarify"
        assert msg.metadata_json.get("reason") == "authentication_required"


@pytest.mark.asyncio
async def test_conversational_greeting_does_not_trigger_handoff() -> None:
    """Conversational greeting like 'Hi' returns a friendly reply and keeps status open."""
    async with async_session_factory() as db_session:
        visitor = Visitor(id=uuid.uuid4(), name="Friendly Visitor")
        db_session.add(visitor)
        await db_session.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db_session.add(conv)
        await db_session.flush()

        fake_provider = FakeLLMProvider()

        msg = await handle_assistant_pipeline(
            db=db_session,
            conversation_id=conv.id,
            visitor_message_content="Hi",
            llm_provider=fake_provider,
        )

        assert msg is not None
        assert msg.sender_type == SenderType.assistant
        assert "Hello! How can I help you today?" in msg.content
        assert msg.metadata_json.get("action") == "answer"
        assert msg.metadata_json.get("reason") == "greeting"
        assert conv.status == ConversationStatus.open


@pytest.mark.asyncio
async def test_customer_identity_query_returns_profile() -> None:
    """Verified customer asking 'What is my name?' receives verified profile details."""
    async with async_session_factory() as db_session:
        visitor = Visitor(id=uuid.uuid4(), name=None, email="verified@example.com")
        db_session.add(visitor)
        await db_session.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db_session.add(conv)
        await db_session.flush()

        session = VisitorSession(
            id=uuid.uuid4(),
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"hash_profile_test_{uuid.uuid4()}",
            origin="http://localhost:5174",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            identity_assurance_level=2,
            verified_customer_id="cust_verified_123",
        )
        db_session.add(session)
        await db_session.commit()

        fake_provider = FakeLLMProvider()
        fake_provider.queue_response({"action": "business_data", "reason": "account_inquiry"})

        msg = await handle_assistant_pipeline(
            db=db_session,
            conversation_id=conv.id,
            visitor_message_content="What is my name?",
            llm_provider=fake_provider,
        )

        assert msg is not None
        assert msg.sender_type == SenderType.assistant
        assert "verified@example.com" in msg.content
        assert "cust_verified_123" in msg.content
        assert msg.metadata_json.get("action") == "answer"


@pytest.mark.asyncio
async def test_customer_order_count_query() -> None:
    """Verified customer asking 'how many orders I have, So far?' receives humane count summary."""
    async with async_session_factory() as db_session:
        visitor = Visitor(id=uuid.uuid4(), name=None, email="verified@example.com")
        db_session.add(visitor)
        await db_session.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db_session.add(conv)
        await db_session.flush()

        session = VisitorSession(
            id=uuid.uuid4(),
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"hash_count_test_{uuid.uuid4()}",
            origin="http://localhost:5174",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            identity_assurance_level=2,
            verified_customer_id="cust_verified_123",
        )
        db_session.add(session)
        await db_session.commit()

        fake_provider = FakeLLMProvider()
        fake_provider.queue_response({"action": "business_data", "reason": "order_count"})

        msg = await handle_assistant_pipeline(
            db=db_session,
            conversation_id=conv.id,
            visitor_message_content="how many orders I have, So far?",
            llm_provider=fake_provider,
        )

        assert msg is not None
        assert msg.sender_type == SenderType.assistant
        assert "You have placed 2 orders with us so far" in msg.content
        assert "ORD-1001" in msg.content
        assert msg.metadata_json.get("action") == "answer"


@pytest.mark.asyncio
async def test_unverified_visitor_account_request_prompts_login() -> None:
    """Anonymous visitor asking about an order without explicit business_data triage is prompted to log in."""
    async with async_session_factory() as db_session:
        visitor = Visitor(id=uuid.uuid4(), name="Anonymous Visitor")
        db_session.add(visitor)
        await db_session.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db_session.add(conv)
        await db_session.flush()

        session = VisitorSession(
            id=uuid.uuid4(),
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"hash_unver_test_{uuid.uuid4()}",
            origin="http://localhost:5174",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            identity_assurance_level=0,
        )
        db_session.add(session)
        await db_session.commit()

        fake_provider = FakeLLMProvider()

        msg = await handle_assistant_pipeline(
            db=db_session,
            conversation_id=conv.id,
            visitor_message_content="Where is order ORD-1001?",
            llm_provider=fake_provider,
        )

        assert msg is not None
        assert msg.sender_type == SenderType.assistant
        assert "Please log in to your store account" in msg.content
        assert msg.metadata_json.get("action") == "clarify"
        assert msg.metadata_json.get("reason") == "authentication_required"
        assert conv.status == ConversationStatus.open
