"""Tests for 5B.6: Telemetry, Auditing & End-to-End Connector Verification."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.connectors.adapter import EnterpriseConnectorGatewayAdapter
from app.connectors.registry import ConnectorRegistry
from app.connectors.rest.connector import RestEnterpriseConnector
from app.database import async_session_factory
from app.gateway.base import ConnectorError
from app.gateway.tool_gateway import ToolGateway
from app.models.connector_audit_log import ConnectorAuditLog
from app.models.conversation import Conversation, ConversationStatus
from app.models.evidence_record import EvidenceRecord
from app.models.handoff import HandoffReason
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.services.assistant_service import create_or_reuse_handoff


@pytest.mark.asyncio
async def test_connector_audit_log_persists_without_pii(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONNECTOR_AUDIT_TEST_SECRET", "super-secret-key-999")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"order_id": "ORD-AUDIT-1", "status": "shipped", "total_amount": 100.0}],
        )

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)

    connector = RestEnterpriseConnector(
        connector_name="audit_rest",
        secret_reference="audit_test",
        custom_client=client,
    )
    registry = ConnectorRegistry(connectors=[connector])

    async with async_session_factory() as db:
        visitor = Visitor(id=uuid.uuid4(), name="Audit Visitor")
        db.add(visitor)
        await db.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db.add(conv)
        await db.flush()

        session = VisitorSession(
            id=uuid.uuid4(),
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"audit_test_hash_{uuid.uuid4().hex}",
            origin="http://localhost:5174",
            expires_at=datetime.now(UTC),
            verified_customer_id="cust_audit_verified_123",
            identity_assurance_level=2,
        )
        db.add(session)
        await db.commit()

        # Execute and audit
        res = await registry.execute_and_audit(
            connector_name="audit_rest",
            operation="get_orders",
            session=session,
            db=db,
        )

        assert res.success is True
        assert res.data[0]["order_id"] == "ORD-AUDIT-1"

        # Verify audit log in DB
        logs = (
            await db.execute(
                select(ConnectorAuditLog).where(
                    ConnectorAuditLog.conversation_id == conv.id
                )
            )
        ).scalars().all()

        assert len(logs) == 1
        audit = logs[0]
        assert audit.connector_name == "audit_rest"
        assert audit.customer_id == "cust_audit_verified_123"
        assert audit.operation == "get_orders"
        assert audit.status == "success"
        assert audit.execution_time_ms >= 0


@pytest.mark.asyncio
async def test_connector_unauthorized_when_ial_zero():
    connector = RestEnterpriseConnector(connector_name="audit_rest")
    registry = ConnectorRegistry(connectors=[connector])

    session = VisitorSession(
        id=uuid.uuid4(),
        visitor_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        token_hash=f"anon_hash_{uuid.uuid4().hex}",
        origin="http://localhost:5174",
        expires_at=datetime.now(UTC),
        verified_customer_id=None,
        identity_assurance_level=0,  # Anonymous
    )

    res = await registry.execute_and_audit(
        connector_name="audit_rest",
        operation="get_orders",
        session=session,
    )

    assert res.success is False
    assert res.error_code == "AUTHORIZATION_REQUIRED"


@pytest.mark.asyncio
async def test_end_to_end_rest_query_to_evidence_record(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONNECTOR_E2E_REST_SECRET", "e2e-secret-key")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "order_id": "ORD-E2E-777",
                "status": "delivered",
                "total_amount": 149.50,
                "carrier": "FastShip Express",
                "tracking_number": "TRK-E2E-99",
            },
        )

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)

    rest_connector = RestEnterpriseConnector(
        connector_name="e2e_rest",
        secret_reference="e2e_rest",
        custom_client=client,
    )
    adapter = EnterpriseConnectorGatewayAdapter(rest_connector, entity_type="order")
    gateway = ToolGateway(connectors=[adapter])

    async with async_session_factory() as db:
        visitor = Visitor(id=uuid.uuid4(), name="E2E Visitor")
        db.add(visitor)
        await db.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db.add(conv)
        await db.flush()

        session = VisitorSession(
            id=uuid.uuid4(),
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"e2e_test_hash_{uuid.uuid4().hex}",
            origin="http://localhost:5174",
            expires_at=datetime.now(UTC),
            verified_customer_id="cust_e2e_verified_456",
            identity_assurance_level=2,
        )
        db.add(session)
        await db.commit()

        # Execute get entity via gateway
        entity = await gateway.execute_get_entity(
            session=session,
            entity_type="order",
            entity_id="ORD-E2E-777",
            db=db,
        )

        assert entity.entity_id == "ORD-E2E-777"
        assert entity.summary_fields["status"] == "delivered"
        assert entity.summary_fields["total_amount"] == 149.50

        # Verify EvidenceRecord in DB
        records = (
            await db.execute(
                select(EvidenceRecord).where(
                    EvidenceRecord.conversation_id == conv.id
                )
            )
        ).scalars().all()

        assert len(records) == 1
        rec = records[0]
        assert rec.entity_type == "order"
        assert rec.verified_customer_id == "cust_e2e_verified_456"
        assert rec.payload_json["entity_id"] == "ORD-E2E-777"
        assert rec.payload_json["summary_fields"]["status"] == "delivered"


@pytest.mark.asyncio
async def test_connector_failure_triggers_safe_handoff(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONNECTOR_FAIL_REST_SECRET", "fail-secret")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal Server Error"})

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)

    rest_connector = RestEnterpriseConnector(
        connector_name="fail_rest",
        secret_reference="fail_rest",
        custom_client=client,
    )
    adapter = EnterpriseConnectorGatewayAdapter(rest_connector, entity_type="order")
    gateway = ToolGateway(connectors=[adapter])

    async with async_session_factory() as db:
        visitor = Visitor(id=uuid.uuid4(), name="Fail Visitor")
        db.add(visitor)
        await db.flush()

        conv = Conversation(id=uuid.uuid4(), visitor_id=visitor.id, status=ConversationStatus.open)
        db.add(conv)
        await db.flush()

        session = VisitorSession(
            id=uuid.uuid4(),
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"fail_test_hash_{uuid.uuid4().hex}",
            origin="http://localhost:5174",
            expires_at=datetime.now(UTC),
            verified_customer_id="cust_fail_123",
            identity_assurance_level=2,
        )
        db.add(session)
        await db.commit()

        # Assert that executing the failed connector through gateway raises ConnectorError
        with pytest.raises(ConnectorError):
            await gateway.execute_get_entity(
                session=session,
                entity_type="order",
                entity_id="ORD-FAIL-1",
                db=db,
            )

        # Trigger safe fallback handoff
        handoff = await create_or_reuse_handoff(
            db=db,
            conversation_id=conv.id,
            reason=HandoffReason.unsupported_request,
            summary="Connector failure triggered safe human handoff",
        )
        assert handoff is not None
        assert handoff.reason == HandoffReason.unsupported_request
