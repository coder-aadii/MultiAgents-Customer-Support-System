"""Tests for Phase 2: Duplicate Document Upload Validation (Item 6) & Staff-Only Failure Reason (Item 4).

Invariants:
1. Uploading a document calculates and records content_hash.
2. Uploading or creating an identical document in the same organization raises HTTP 409 Conflict (DUPLICATE_DOCUMENT).
3. When AI triggers handoff, metadata_json contains structured staff_diagnostic details.
4. Visitor-facing endpoints (/api/v1/widget/messages) strictly strip staff_diagnostic so visitors never see internal diagnostics.
5. Staff-facing conversation endpoints preserve staff_diagnostic for operator inspection.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import async_session_factory
from app.main import app
from app.models.conversation import Conversation, ConversationStatus
from app.models.handoff import HandoffReason
from app.models.knowledge_document import DocumentStatus, KnowledgeDocument
from app.models.message import Message, SenderType
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.schemas.message import MessageResponse
from app.security.password import hash_password
from app.security.tokens import create_access_jwt
from app.services.assistant_service import _trigger_staff_handoff
from app.services.multi_agent_service import _handoff as multi_agent_handoff
from app.api.routes.widget import _sanitize_visitor_message


@pytest.fixture
async def setup_phase_2_env():
    """Setup users, organization, visitor, and conversation for Phase 2 tests."""
    async with async_session_factory() as session:
        admin_id = uuid.uuid4()
        admin = User(
            id=admin_id,
            email=f"p2_admin_{admin_id.hex[:6]}@example.com",
            password_hash=hash_password("AdminPass123!"),
            display_name="Phase 2 Admin",
            role=UserRole.admin,
            is_active=True,
        )
        session.add(admin)

        staff_id = uuid.uuid4()
        staff = User(
            id=staff_id,
            email=f"p2_staff_{staff_id.hex[:6]}@example.com",
            password_hash=hash_password("StaffPass123!"),
            display_name="Phase 2 Staff",
            role=UserRole.staff,
            is_active=True,
        )
        session.add(staff)
        await session.flush()

        now = datetime.now(UTC)
        visitor = Visitor(
            name="Phase 2 Visitor",
            email="p2_vis@example.com",
            created_at=now,
            last_seen_at=now,
        )
        session.add(visitor)
        await session.flush()

        conv = Conversation(
            visitor_id=visitor.id,
            status=ConversationStatus.open,
            created_at=now,
            updated_at=now,
        )
        session.add(conv)
        await session.flush()

        token = f"tok_p2_{uuid.uuid4()}"
        vis_session = VisitorSession(
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            origin="http://localhost:5174",
            expires_at=now + timedelta(hours=24),
            created_at=now,
        )
        session.add(vis_session)
        await session.commit()

        admin_token = create_access_jwt(str(admin.id), "admin")
        staff_token = create_access_jwt(str(staff.id), "staff")

        return {
            "admin": admin,
            "admin_token": admin_token,
            "staff": staff,
            "staff_token": staff_token,
            "conv_id": conv.id,
            "vis_token": token,
        }


# ==============================================================================
# Item 6: Duplicate Document Upload Validation Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_upload_duplicate_document_rejection(setup_phase_2_env):
    """Uploading the same file twice to the same organization must return HTTP 409 Conflict."""
    admin_token = setup_phase_2_env["admin_token"]
    file_bytes = b"Return policy details: returns are accepted within 30 days of delivery."
    files = {"file": ("return_policy.txt", file_bytes, "text/plain")}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. First upload succeeds
        res1 = await client.post(
            "/api/v1/knowledge/documents/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files=files,
            data={"title": "Return Policy Initial"},
        )
        assert res1.status_code == 201
        doc1_id = res1.json()["id"]

        # 2. Second upload with identical bytes must return HTTP 409 Conflict
        res2 = await client.post(
            "/api/v1/knowledge/documents/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("return_policy_copy.txt", file_bytes, "text/plain")},
            data={"title": "Return Policy Duplicate"},
        )
        assert res2.status_code == 409
        err = res2.json()["error"]
        assert err["code"] == "DUPLICATE_DOCUMENT"
        assert "already exists" in err["message"]
        assert err["details"]["existing_document_id"] == doc1_id


@pytest.mark.asyncio
async def test_create_duplicate_document_rejection(setup_phase_2_env):
    """Creating a document via POST /documents with identical content must return HTTP 409 Conflict."""
    admin_token = setup_phase_2_env["admin_token"]
    content = "Warranty policy: all hardware is covered by a 1-year limited warranty."

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. First manual document creation succeeds
        res1 = await client.post(
            "/api/v1/knowledge/documents",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": "Warranty Policy", "source_type": "text", "content": content},
        )
        assert res1.status_code == 201
        doc1_id = res1.json()["id"]

        # 2. Second creation with identical content returns 409
        res2 = await client.post(
            "/api/v1/knowledge/documents",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": "Warranty Policy Duplicate", "source_type": "text", "content": content},
        )
        assert res2.status_code == 409
        assert res2.json()["error"]["code"] == "DUPLICATE_DOCUMENT"
        assert res2.json()["error"]["details"]["existing_document_id"] == doc1_id


@pytest.mark.asyncio
async def test_update_document_duplicate_content_rejection(setup_phase_2_env):
    """Editing a draft document to match existing document content must return HTTP 409 Conflict."""
    admin_token = setup_phase_2_env["admin_token"]
    content_a = "Policy A: Distinct terms and conditions."
    content_b = "Policy B: Completely different conditions."

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create Doc 1
        res1 = await client.post(
            "/api/v1/knowledge/documents",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": "Policy A Doc", "source_type": "text", "content": content_a},
        )
        assert res1.status_code == 201
        doc1_id = res1.json()["id"]

        # Create Doc 2 with distinct content
        res2 = await client.post(
            "/api/v1/knowledge/documents",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": "Policy B Doc", "source_type": "text", "content": content_b},
        )
        assert res2.status_code == 201
        doc2_id = res2.json()["id"]

        # Edit Doc 2's content to collide with Doc 1 -> Must raise 409 Conflict
        res_update = await client.patch(
            f"/api/v1/knowledge/documents/{doc2_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"content": content_a},
        )
        assert res_update.status_code == 409
        assert res_update.json()["error"]["code"] == "DUPLICATE_DOCUMENT"
        assert res_update.json()["error"]["details"]["existing_document_id"] == doc1_id


# ==============================================================================
# Item 4: Staff-Only AI Failure Reason in Chat Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_staff_diagnostic_present_in_handoff_message(setup_phase_2_env):
    """When _trigger_staff_handoff is invoked, message metadata contains staff_diagnostic."""
    conv_id = setup_phase_2_env["conv_id"]
    async with async_session_factory() as db:
        conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one()
        now = datetime.now(UTC)

        msg = await _trigger_staff_handoff(
            db=db,
            active_conv=conv,
            conversation_id=conv_id,
            reason=HandoffReason.low_confidence,
            summary="Low confidence score: 0.52 < 0.75",
            now=now,
        )

        assert msg.metadata_json is not None
        assert "staff_diagnostic" in msg.metadata_json
        diag = msg.metadata_json["staff_diagnostic"]
        assert diag["code"] == "low_confidence"
        assert diag["failure_stage"] == "assistant_pipeline"
        assert "0.52" in diag["detail"]


@pytest.mark.asyncio
async def test_staff_diagnostic_stripped_from_widget_messages(setup_phase_2_env):
    """GET /api/v1/widget/messages strictly strips staff_diagnostic from visitor view."""
    conv_id = setup_phase_2_env["conv_id"]
    vis_token = setup_phase_2_env["vis_token"]
    staff_token = setup_phase_2_env["staff_token"]

    async with async_session_factory() as db:
        now = datetime.now(UTC)
        # Create an assistant message with internal staff_diagnostic
        msg = Message(
            conversation_id=conv_id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.assistant,
            content="Connecting you to staff...",
            metadata_json={
                "action": "handoff",
                "reason": "no_evidence_available",
                "staff_diagnostic": {
                    "code": "NO_EVIDENCE_AVAILABLE",
                    "failure_stage": "retrieval",
                    "detail": "Vector similarity below 0.70 threshold",
                },
            },
            created_at=now,
        )
        db.add(msg)
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Visitor view via widget endpoint -> staff_diagnostic MUST NOT be present
        w_resp = await client.get(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {vis_token}", "Origin": "http://localhost:5174"},
        )
        assert w_resp.status_code == 200
        items = w_resp.json()["items"]
        assert len(items) > 0
        asst_msg = next(m for m in items if m["sender_type"] == "assistant")
        assert "staff_diagnostic" not in asst_msg["metadata"]

        # 2. Staff view via conversations endpoint -> staff_diagnostic MUST be preserved
        s_resp = await client.get(
            f"/api/v1/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {staff_token}"},
        )
        assert s_resp.status_code == 200
        staff_items = s_resp.json()["items"]
        staff_asst_msg = next(m for m in staff_items if m["sender_type"] == "assistant")
        assert "staff_diagnostic" in staff_asst_msg["metadata"]
        assert staff_asst_msg["metadata"]["staff_diagnostic"]["code"] == "NO_EVIDENCE_AVAILABLE"


@pytest.mark.asyncio
async def test_duplicate_document_multi_tenant_isolation(setup_phase_2_env):
    """Identical document in Org A must NOT block Org B from uploading it."""
    admin_token_a = setup_phase_2_env["admin_token"]
    content = "Unique multi-tenant test document content for tenant isolation."

    # Create Org B and Admin B
    async with async_session_factory() as session:
        org_b = Organization(name="Tenant B", slug=f"tenant-b-{uuid.uuid4().hex[:6]}")
        session.add(org_b)
        await session.flush()

        admin_b = User(
            email=f"admin_b_{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("Pass123!"),
            display_name="Admin B",
            role=UserRole.admin,
            organization_id=org_b.id,
            is_active=True,
        )
        session.add(admin_b)
        await session.commit()
        admin_token_b = create_access_jwt(str(admin_b.id), "admin")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Org A uploads document
        res_a = await client.post(
            "/api/v1/knowledge/documents",
            headers={"Authorization": f"Bearer {admin_token_a}"},
            json={"title": "Doc in Org A", "source_type": "text", "content": content},
        )
        assert res_a.status_code == 201

        # Org B uploads identical content -> MUST succeed because of tenant scoping
        res_b = await client.post(
            "/api/v1/knowledge/documents",
            headers={"Authorization": f"Bearer {admin_token_b}"},
            json={"title": "Doc in Org B", "source_type": "text", "content": content},
        )
        assert res_b.status_code == 201
        assert res_b.json()["content_hash"] == res_a.json()["content_hash"]


@pytest.mark.asyncio
async def test_archived_document_can_be_reuploaded(setup_phase_2_env):
    """Archiving a document must free its content_hash to allow re-upload."""
    admin_token = setup_phase_2_env["admin_token"]
    content = "Deprecating old return policy content for archive re-upload test."

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create document
        res1 = await client.post(
            "/api/v1/knowledge/documents",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": "Archivable Doc", "source_type": "text", "content": content},
        )
        assert res1.status_code == 201
        doc_id = res1.json()["id"]

        # 2. Archive document
        async with async_session_factory() as session:
            doc = (await session.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == uuid.UUID(doc_id)))).scalar_one()
            doc.status = DocumentStatus.archived
            await session.commit()

        # 3. Uploading identical content again should succeed now that old doc is archived
        res2 = await client.post(
            "/api/v1/knowledge/documents",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": "Re-uploaded Doc", "source_type": "text", "content": content},
        )
        assert res2.status_code == 201


@pytest.mark.asyncio
async def test_multi_agent_handoff_populates_staff_diagnostic(setup_phase_2_env):
    """multi_agent_service._handoff must populate staff_diagnostic in metadata_json."""
    conv_id = setup_phase_2_env["conv_id"]
    async with async_session_factory() as db:
        conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one()
        msg = await multi_agent_handoff(db, conv, "synthesis_verification_failed")
        assert msg is not None
        assert "staff_diagnostic" in msg.metadata_json
        diag = msg.metadata_json["staff_diagnostic"]
        assert diag["failure_stage"] == "multi_agent_orchestration"
        assert diag["code"] == "WORKFLOW_HALTED"
        assert "synthesis_verification_failed" in diag["detail"]


@pytest.mark.asyncio
async def test_sanitize_visitor_message_edge_cases_fuzz():
    """Fuzz _sanitize_visitor_message with edge and boundary inputs."""
    base_id = uuid.uuid4()
    now = datetime.now(UTC)

    # 1. Message without metadata
    m1 = MessageResponse(
        id=base_id,
        conversation_id=base_id,
        client_message_id=None,
        sender_type=SenderType.assistant,
        sender_user_id=None,
        content="Clean message",
        metadata={},
        created_at=now,
    )
    s1 = _sanitize_visitor_message(m1)
    assert s1.metadata == {}

    # 2. Message with staff_diagnostic plus customer-facing metadata
    m2 = MessageResponse(
        id=base_id,
        conversation_id=base_id,
        client_message_id=None,
        sender_type=SenderType.assistant,
        sender_user_id=None,
        content="Handoff message",
        metadata={
            "action": "handoff",
            "reason": "customer_facing_reason",
            "staff_diagnostic": {"code": "INTERNAL_LEAK", "detail": "Secret system info"},
        },
        created_at=now,
    )
    s2 = _sanitize_visitor_message(m2)
    assert "staff_diagnostic" not in s2.metadata
    assert s2.metadata["action"] == "handoff"
    assert s2.metadata["reason"] == "customer_facing_reason"


@pytest.mark.asyncio
async def test_edit_document_duplicate_hash_conflict(setup_phase_2_env):
    """Editing a draft document's content to match another existing document must return HTTP 409."""
    admin_token = setup_phase_2_env["admin_token"]
    content_a = "Original Content Alpha for unique testing."
    content_b = "Original Content Beta for unique testing."

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create Doc A
        res_a = await client.post(
            "/api/v1/knowledge/documents",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": "Doc Alpha", "source_type": "text", "content": content_a},
        )
        assert res_a.status_code == 201

        # Create Doc B
        res_b = await client.post(
            "/api/v1/knowledge/documents",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"title": "Doc Beta", "source_type": "text", "content": content_b},
        )
        assert res_b.status_code == 201
        doc_b_id = res_b.json()["id"]

        # Edit Doc B to have content of Doc A -> Must fail with 409 Conflict
        res_patch = await client.patch(
            f"/api/v1/knowledge/documents/{doc_b_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"content": content_a},
        )
        assert res_patch.status_code == 409
        assert res_patch.json()["error"]["code"] == "DUPLICATE_DOCUMENT"



