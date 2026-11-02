"""Tests for Sub-phase 5A.3: Universal Tool Gateway & Mock Business Connectors."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.database import async_session_factory
from app.gateway import (
    BaseConnector,
    CapabilityDefinition,
    ConnectorError,
    EntityNotFoundError,
    EntityRecord,
    MockBusinessConnector,
    ToolAuthorizationError,
    ToolGateway,
    ToolTimeoutError,
    ToolValidationError,
)
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation, ConversationStatus
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.schemas.evidence import TurnEvidenceRegistry


@pytest.fixture
async def setup_test_sessions():
    """Create visitor sessions with different assurance levels and states."""
    async with async_session_factory() as db:
        now = datetime.now(UTC)

        visitor = Visitor(
            name="Gateway Test Visitor",
            email="gateway@test.com",
            created_at=now,
            last_seen_at=now,
        )
        db.add(visitor)
        await db.flush()

        conv = Conversation(
            visitor_id=visitor.id,
            status=ConversationStatus.open,
            created_at=now,
            updated_at=now,
        )
        db.add(conv)
        await db.flush()

        # 1. Verified Session (IAL 2) - Customer 123
        session_verified = VisitorSession(
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=hashlib.sha256(f"tok_ver_{uuid.uuid4()}".encode()).hexdigest(),
            origin="http://localhost:5174",
            expires_at=now + timedelta(hours=24),
            verified_customer_id="cust_verified_123",
            customer_token_hash=hashlib.sha256(b"dummy_token_hash").hexdigest(),
            identity_assurance_level=2,
            customer_token_expires_at=now + timedelta(hours=2),
            created_at=now,
        )
        db.add(session_verified)

        # 2. Anonymous Session (IAL 0)
        session_anon = VisitorSession(
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=hashlib.sha256(f"tok_anon_{uuid.uuid4()}".encode()).hexdigest(),
            origin="http://localhost:5174",
            expires_at=now + timedelta(hours=24),
            verified_customer_id=None,
            customer_token_hash=None,
            identity_assurance_level=0,
            customer_token_expires_at=None,
            created_at=now,
        )
        db.add(session_anon)

        # 3. Claimed Session (IAL 1)
        session_claimed = VisitorSession(
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=hashlib.sha256(f"tok_claim_{uuid.uuid4()}".encode()).hexdigest(),
            origin="http://localhost:5174",
            expires_at=now + timedelta(hours=24),
            verified_customer_id=None,
            customer_token_hash=None,
            identity_assurance_level=1,
            customer_token_expires_at=None,
            created_at=now,
        )
        db.add(session_claimed)

        # 4. Expired Token Session (IAL 2 but customer token expired)
        session_expired = VisitorSession(
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=hashlib.sha256(f"tok_exp_{uuid.uuid4()}".encode()).hexdigest(),
            origin="http://localhost:5174",
            expires_at=now + timedelta(hours=24),
            verified_customer_id="cust_verified_123",
            customer_token_hash=hashlib.sha256(b"dummy_token_hash").hexdigest(),
            identity_assurance_level=2,
            customer_token_expires_at=now - timedelta(minutes=10),  # expired
            created_at=now - timedelta(hours=1),
        )
        db.add(session_expired)

        # 5. Revoked Session
        session_revoked = VisitorSession(
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=hashlib.sha256(f"tok_rev_{uuid.uuid4()}".encode()).hexdigest(),
            origin="http://localhost:5174",
            expires_at=now + timedelta(hours=24),
            verified_customer_id="cust_verified_123",
            customer_token_hash=hashlib.sha256(b"dummy_token_hash").hexdigest(),
            identity_assurance_level=2,
            customer_token_expires_at=now + timedelta(hours=2),
            revoked_at=now - timedelta(minutes=5),  # revoked
            created_at=now - timedelta(hours=1),
        )
        db.add(session_revoked)

        await db.commit()
        await db.refresh(session_verified)
        await db.refresh(session_anon)
        await db.refresh(session_claimed)
        await db.refresh(session_expired)
        await db.refresh(session_revoked)

        return {
            "verified": session_verified,
            "anon": session_anon,
            "claimed": session_claimed,
            "expired": session_expired,
            "revoked": session_revoked,
            "conversation_id": conv.id,
        }


# =========================================================================
# 1. Mock Connector Tests
# =========================================================================


@pytest.mark.asyncio
async def test_mock_connector_deterministic_fixtures():
    """Verify MockBusinessConnector retrieves seeded orders, shipments, and refunds."""
    connector = MockBusinessConnector()

    # Verify orders
    order1 = await connector.get_entity_summary("order", "ORD-1001", "cust_verified_123")
    assert order1.entity_id == "ORD-1001"
    assert order1.summary_fields["status"] == "delivered"
    assert order1.summary_fields["total_amount"] == 129.99
    assert order1.summary_fields["carrier"] == "FastShip Express"

    order2 = await connector.get_entity_summary("order", "ORD-1002", "cust_verified_123")
    assert order2.entity_id == "ORD-1002"
    assert order2.summary_fields["status"] == "in_transit"
    assert order2.summary_fields["estimated_delivery"] == "2026-11-05"

    # Verify shipments
    shipment = await connector.get_entity_summary("shipment", "TRK-8801", "cust_verified_123")
    assert shipment.entity_id == "TRK-8801"
    assert shipment.summary_fields["status"] == "delivered"
    assert shipment.summary_fields["carrier"] == "FastShip Express"

    # Verify refunds
    refund = await connector.get_entity_summary("refund", "REF-5001", "cust_verified_123")
    assert refund.entity_id == "REF-5001"
    assert refund.summary_fields["status"] == "completed"
    assert refund.summary_fields["amount"] == 25.00


@pytest.mark.asyncio
async def test_mock_connector_list_customer_entities():
    """Verify listing entities filters strictly by customer_id and entity_type."""
    connector = MockBusinessConnector()

    orders_123 = await connector.list_customer_entities("order", "cust_verified_123")
    assert len(orders_123) == 2
    order_ids = {o.entity_id for o in orders_123}
    assert order_ids == {"ORD-1001", "ORD-1002"}

    # Other customer's orders are isolated
    orders_other = await connector.list_customer_entities("order", "cust_other_456")
    assert len(orders_other) == 1
    assert orders_other[0].entity_id == "ORD-2001"

    # Customer with zero records
    orders_empty = await connector.list_customer_entities("order", "cust_nonexistent")
    assert orders_empty == []


# =========================================================================
# 2. Tool Gateway Zero-Trust Authorization & Customer Injection Tests
# =========================================================================


@pytest.mark.asyncio
async def test_tool_gateway_injects_verified_customer_id(setup_test_sessions):
    """Verify Tool Gateway injects verified_customer_id server-side from session."""
    session = setup_test_sessions["verified"]
    gateway = ToolGateway()

    # The caller provides only entity_id; gateway injects customer_id = "cust_verified_123"
    entity = await gateway.execute_get_entity(
        session=session,
        entity_type="order",
        entity_id="ORD-1001",
    )

    assert entity.entity_id == "ORD-1001"
    assert entity.customer_id == "cust_verified_123"
    assert entity.summary_fields["status"] == "delivered"


@pytest.mark.asyncio
async def test_cross_customer_isolation_raises_not_found(setup_test_sessions):
    """Verify requesting an entity belonging to another customer raises EntityNotFoundError (preventing BOLA/IDOR)."""
    session = setup_test_sessions["verified"]  # customer "cust_verified_123"
    gateway = ToolGateway()

    # ORD-2001 belongs to "cust_other_456"
    with pytest.raises(EntityNotFoundError) as exc_info:
        await gateway.execute_get_entity(
            session=session,
            entity_type="order",
            entity_id="ORD-2001",
        )

    # Must report not found, not unauthorized or forbidden (preventing ID enumeration)
    assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_unverified_session_rejected_before_tool_execution(setup_test_sessions):
    """Verify sessions with IAL < 2 are rejected immediately before any tool is invoked."""
    gateway = ToolGateway()

    # Anonymous session (IAL 0)
    with pytest.raises(ToolAuthorizationError) as exc_info_anon:
        await gateway.execute_get_entity(
            session=setup_test_sessions["anon"],
            entity_type="order",
            entity_id="ORD-1001",
        )
    assert "IAL 0" in str(exc_info_anon.value)

    # Claimed session (IAL 1)
    with pytest.raises(ToolAuthorizationError) as exc_info_claimed:
        await gateway.execute_get_entity(
            session=setup_test_sessions["claimed"],
            entity_type="order",
            entity_id="ORD-1001",
        )
    assert "IAL 1" in str(exc_info_claimed.value)


@pytest.mark.asyncio
async def test_expired_token_session_rejected(setup_test_sessions):
    """Verify session whose customer token has expired is rejected."""
    gateway = ToolGateway()

    with pytest.raises(ToolAuthorizationError) as exc_info:
        await gateway.execute_get_entity(
            session=setup_test_sessions["expired"],
            entity_type="order",
            entity_id="ORD-1001",
        )
    assert "expired" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_revoked_session_rejected(setup_test_sessions):
    """Verify revoked session is rejected immediately."""
    gateway = ToolGateway()

    with pytest.raises(ToolAuthorizationError) as exc_info:
        await gateway.execute_get_entity(
            session=setup_test_sessions["revoked"],
            entity_type="order",
            entity_id="ORD-1001",
        )
    assert "revoked" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_caller_supplied_customer_id_rejected(setup_test_sessions):
    """Verify that caller attempting to supply or override customer_id in execute_tool is rejected."""
    gateway = ToolGateway()

    with pytest.raises(ToolValidationError) as exc_info:
        await gateway.execute_tool(
            session=setup_test_sessions["verified"],
            capability_name="get_order_summary",
            parameters={"entity_id": "ORD-1001", "customer_id": "cust_other_456"},
        )
    assert "caller cannot supply or override customer_id" in str(exc_info.value)


# =========================================================================
# 3. Timeout, Error Handling & Audit Logging Tests
# =========================================================================


class SlowMockConnector(BaseConnector):
    """Connector simulating slow upstream response exceeding timeout."""

    @property
    def connector_name(self) -> str:
        return "slow_connector"

    @property
    def supported_entity_types(self) -> list[str]:
        return ["slow_entity"]

    def get_capabilities(self) -> list[CapabilityDefinition]:
        return []

    async def get_entity_summary(
        self, entity_type: str, entity_id: str, customer_id: str
    ) -> EntityRecord:
        await asyncio.sleep(1.0)
        return EntityRecord(
            entity_type=entity_type,
            entity_id=entity_id,
            customer_id=customer_id,
            title=f"Slow {entity_id}",
        )

    async def list_customer_entities(
        self, entity_type: str, customer_id: str
    ) -> list[EntityRecord]:
        await asyncio.sleep(1.0)
        return []


@pytest.mark.asyncio
async def test_tool_gateway_timeout_enforcement(setup_test_sessions):
    """Verify that connector exceeding timeout triggers ToolTimeoutError."""
    gateway = ToolGateway(connectors=[SlowMockConnector()])

    with pytest.raises(ToolTimeoutError) as exc_info:
        await gateway.execute_get_entity(
            session=setup_test_sessions["verified"],
            entity_type="slow_entity",
            entity_id="SLOW-1",
            timeout_seconds=0.1,  # Bounded short timeout
        )

    assert "timed out after 0.1s" in str(exc_info.value)


@pytest.mark.asyncio
async def test_audit_event_emitted_on_execution(setup_test_sessions):
    """Verify that executing a tool emits a sanitized tool.execute audit event."""
    session = setup_test_sessions["verified"]
    gateway = ToolGateway()

    async with async_session_factory() as db:
        entity = await gateway.execute_get_entity(
            session=session,
            entity_type="order",
            entity_id="ORD-1001",
            db=db,
        )
        assert entity.entity_id == "ORD-1001"
        await db.commit()

        # Query audit events for this conversation
        stmt = select(AuditEvent).where(
            AuditEvent.event_type == "tool.execute",
            AuditEvent.resource_id == session.conversation_id,
        )
        result = await db.execute(stmt)
        events = result.scalars().all()

        assert len(events) >= 1
        audit = events[-1]
        assert audit.event_type == "tool.execute"
        assert audit.resource_type == "business_entity"
        assert audit.actor_id == session.visitor_id
        assert audit.metadata_json["operation"] == "get_entity"
        assert audit.metadata_json["entity_type"] == "order"
        assert audit.metadata_json["entity_id"] == "ORD-1001"
        assert audit.metadata_json["customer_id"] == "cust_verified_123"
        assert audit.metadata_json["success"] is True
        assert "execution_time_ms" in audit.metadata_json
        # Invariant: No customer PII or raw secrets in audit metadata
        assert "password" not in audit.metadata_json
        assert "shipping_address" not in audit.metadata_json


@pytest.mark.asyncio
async def test_evidence_registry_receives_record(setup_test_sessions):
    """Verify that passing TurnEvidenceRegistry registers an EvidenceRecordCreate with the entity."""
    session = setup_test_sessions["verified"]
    gateway = ToolGateway()
    registry = TurnEvidenceRegistry(conversation_id=session.conversation_id)

    entity = await gateway.execute_get_entity(
        session=session,
        entity_type="order",
        entity_id="ORD-1001",
        registry=registry,
    )
    assert entity.entity_id == "ORD-1001"

    # Registry must contain the newly retrieved record
    assert len(registry.records) == 1
    rec = registry.records[0]
    assert rec.evidence_key == "order:ORD-1001"
    assert rec.title == "Order #ORD-1001"
    assert rec.verified_customer_id == "cust_verified_123"
    assert rec.payload_json["summary_fields"]["status"] == "delivered"


@pytest.mark.asyncio
async def test_entity_record_to_evidence_record_and_fact_extraction():
    """Verify EntityRecord correctly creates EvidenceRecordCreate and extracts VerifiedFacts."""
    entity = EntityRecord(
        entity_type="order",
        entity_id="ORD-1001",
        customer_id="cust_verified_123",
        title="Order #ORD-1001",
        summary_fields={
            "status": "delivered",
            "total_amount": 129.99,
            "carrier": "FastShip Express",
            "is_gift": False,
        },
        raw_payload={"secret_internal_margin": 45.00},
    )

    # 1. Test EvidenceRecordCreate conversion
    evidence_create = entity.to_evidence_record_create(expires_in_days=14)
    assert evidence_create.evidence_key == "order:ORD-1001"
    assert evidence_create.title == "Order #ORD-1001"
    assert evidence_create.verified_customer_id == "cust_verified_123"
    assert evidence_create.expires_in_days == 14
    assert evidence_create.payload_json["summary_fields"]["status"] == "delivered"

    # 2. Test VerifiedFact extraction
    facts = entity.extract_facts(source_evidence_id="ref_abc123")
    assert len(facts) == 4
    fact_dict = {f.field_name: f.field_value for f in facts}
    assert fact_dict["status"] == "delivered"
    assert fact_dict["total_amount"] == 129.99
    assert fact_dict["carrier"] == "FastShip Express"
    assert fact_dict["is_gift"] == "false"
    assert all(f.source_evidence_id == "ref_abc123" for f in facts)
    assert all(f.entity_id == "ORD-1001" for f in facts)
    assert all(f.entity_type == "order" for f in facts)


@pytest.mark.asyncio
async def test_tool_gateway_capabilities_discovery():
    """Verify Tool Gateway discovers and lists all registered connector capabilities."""
    gateway = ToolGateway()
    capabilities = gateway.list_capabilities()

    assert len(capabilities) >= 4
    cap_names = {c.name for c in capabilities}
    assert "get_order_summary" in cap_names
    assert "list_customer_orders" in cap_names
    assert "get_shipment_status" in cap_names
    assert "get_refund_status" in cap_names
    assert all(c.requires_ial == 2 for c in capabilities)


@pytest.mark.asyncio
async def test_tool_gateway_unknown_entity_type_raises_error(setup_test_sessions):
    """Verify requesting an unsupported entity type raises ConnectorError."""
    gateway = ToolGateway()

    with pytest.raises(ConnectorError) as exc_info:
        await gateway.execute_get_entity(
            session=setup_test_sessions["verified"],
            entity_type="unsupported_widget",
            entity_id="WIDGET-1",
        )
    assert "No connector registered" in str(exc_info.value)
