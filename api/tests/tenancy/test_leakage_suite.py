from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models.assistant_job import AssistantJob, AssistantJobStatus
from app.models.conversation import Conversation, ConversationStatus
from app.models.evidence_record import EvidenceRecord
from app.models.knowledge_document import DocumentStatus, IngestionStatus, KnowledgeDocument
from app.models.message import Message, SenderType
from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.models.user import User, UserRole
from app.models.visitor import Visitor
from app.security.password import hash_password
from app.security.tokens import create_access_jwt
from app.services.job_worker import claim_next_job
from app.tenancy.context import TenantContext
from app.tenancy.filter import TenantQueryScoper


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def two_tenants(async_db: AsyncSession) -> tuple[Organization, Organization]:
    org_a = Organization(
        slug=f"pen-org-a-{uuid.uuid4().hex[:6]}",
        name="Penetration Org Alpha",
        is_active=True,
    )
    org_b = Organization(
        slug=f"pen-org-b-{uuid.uuid4().hex[:6]}",
        name="Penetration Org Beta",
        is_active=True,
    )
    async_db.add_all([org_a, org_b])
    await async_db.commit()
    return org_a, org_b


@pytest.mark.asyncio
async def test_cross_tenant_message_injection_blocked(
    async_db: AsyncSession, two_tenants: tuple[Organization, Organization]
):
    """Attempting to access or list messages for an Org A conversation while scoped to Org B returns empty."""
    org_a, org_b = two_tenants

    visitor_a = Visitor(organization_id=org_a.id, name="Victim A")
    async_db.add(visitor_a)
    await async_db.flush()

    conv_a = Conversation(
        organization_id=org_a.id,
        visitor_id=visitor_a.id,
        status=ConversationStatus.open,
    )
    async_db.add(conv_a)
    await async_db.flush()

    msg_a = Message(
        organization_id=org_a.id,
        conversation_id=conv_a.id,
        sender_type=SenderType.visitor,
        content="Secret financial details for Org A",
    )
    async_db.add(msg_a)
    await async_db.commit()

    # Org B queries messages
    tokens = TenantContext.set_tenant(org_b.id, org_b.slug)
    try:
        stmt = TenantQueryScoper.apply_scope(
            select(Message).where(Message.conversation_id == conv_a.id),
            Message,
        )
        res = await async_db.execute(stmt)
        messages_found = list(res.scalars().all())

        assert len(messages_found) == 0
    finally:
        TenantContext.reset(tokens)


@pytest.mark.asyncio
async def test_cross_tenant_evidence_access_blocked(
    async_db: AsyncSession, two_tenants: tuple[Organization, Organization]
):
    """Evidence records created under Org A cannot be retrieved by Org B."""
    org_a, org_b = two_tenants

    visitor_a = Visitor(organization_id=org_a.id, name="Evidence User A")
    async_db.add(visitor_a)
    await async_db.flush()

    conv_a = Conversation(
        organization_id=org_a.id,
        visitor_id=visitor_a.id,
        status=ConversationStatus.open,
    )
    async_db.add(conv_a)
    await async_db.flush()

    evidence_a = EvidenceRecord(
        organization_id=org_a.id,
        conversation_id=conv_a.id,
        evidence_key="order_details:ORD-77",
        source_type="enterprise_api",
        title="Order #77 Invoice",
        entity_type="order",
        payload_json={"total": 999.00, "customer": "VIP Org A"},
        expires_at=datetime.now(UTC),
    )
    async_db.add(evidence_a)
    await async_db.commit()

    # Query with Org B scope
    scoped_evidence = await TenantQueryScoper.get_scoped(
        db=async_db,
        model=EvidenceRecord,
        entity_id=evidence_a.id,
        org_id=org_b.id,
    )
    assert scoped_evidence is None

    # Query with Org A scope
    authorized_evidence = await TenantQueryScoper.get_scoped(
        db=async_db,
        model=EvidenceRecord,
        entity_id=evidence_a.id,
        org_id=org_a.id,
    )
    assert authorized_evidence is not None
    assert authorized_evidence.id == evidence_a.id


@pytest.mark.asyncio
async def test_async_job_worker_preserves_tenant_context(
    async_db: AsyncSession, two_tenants: tuple[Organization, Organization]
):
    """Background assistant jobs maintain strict organization_id isolation."""
    org_a, org_b = two_tenants

    visitor_a = Visitor(organization_id=org_a.id, name="Job User")
    async_db.add(visitor_a)
    await async_db.flush()

    conv_a = Conversation(
        organization_id=org_a.id,
        visitor_id=visitor_a.id,
        status=ConversationStatus.open,
    )
    async_db.add(conv_a)
    await async_db.flush()

    msg_a = Message(
        organization_id=org_a.id,
        conversation_id=conv_a.id,
        sender_type=SenderType.visitor,
        content="Help with account",
    )
    async_db.add(msg_a)
    await async_db.flush()

    job_a = AssistantJob(
        organization_id=org_a.id,
        conversation_id=conv_a.id,
        message_id=msg_a.id,
        status=AssistantJobStatus.queued.value,
        attempts=0,
        max_attempts=2,
        created_at=datetime.now(UTC),
    )
    async_db.add(job_a)
    await async_db.commit()

    # Claim job using worker
    claimed = await claim_next_job(
        db=async_db,
        worker_id="test-worker-1",
        job_id=job_a.id,
    )
    await async_db.commit()

    assert claimed is not None
    assert claimed.id == job_a.id
    assert claimed.organization_id == org_a.id


@pytest.mark.asyncio
async def test_http_cross_tenant_conversation_leakage_blocked(
    async_db: AsyncSession, two_tenants: tuple[Organization, Organization]
):
    """Staff user of Org B cannot access Org A conversations via HTTP endpoint (returns 404)."""
    org_a, org_b = two_tenants

    # Create Staff user for Org A
    user_a = User(
        organization_id=org_a.id,
        email=f"agent-a-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Password123!"),
        display_name="Agent Alpha",
        role=UserRole.staff,
        is_active=True,
    )
    # Create Staff user for Org B
    user_b = User(
        organization_id=org_b.id,
        email=f"agent-b-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Password123!"),
        display_name="Agent Beta",
        role=UserRole.staff,
        is_active=True,
    )
    async_db.add_all([user_a, user_b])
    await async_db.flush()

    # Create conversation in Org A
    visitor_a = Visitor(organization_id=org_a.id, name="Customer Alpha")
    async_db.add(visitor_a)
    await async_db.flush()

    conv_a = Conversation(
        organization_id=org_a.id,
        visitor_id=visitor_a.id,
        status=ConversationStatus.open,
    )
    async_db.add(conv_a)
    await async_db.commit()

    token_a = create_access_jwt(
        subject=str(user_a.id),
        role=user_a.role.value,
        additional_claims={"org_id": str(org_a.id), "org_slug": org_a.slug},
    )
    token_b = create_access_jwt(
        subject=str(user_b.id),
        role=user_b.role.value,
        additional_claims={"org_id": str(org_b.id), "org_slug": org_b.slug},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Org A staff can access conv_a
        resp_a = await client.get(
            f"/api/v1/conversations/{conv_a.id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a.status_code == 200
        assert resp_a.json()["id"] == str(conv_a.id)

        # Org B staff MUST receive 404 Not Found when trying to access conv_a
        resp_b = await client.get(
            f"/api/v1/conversations/{conv_a.id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.status_code == 404

        # Org B staff listing conversations must NOT see conv_a
        resp_b_list = await client.get(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b_list.status_code == 200
        conv_ids = [item["id"] for item in resp_b_list.json()["items"]]
        assert str(conv_a.id) not in conv_ids


@pytest.mark.asyncio
async def test_http_cross_tenant_knowledge_leakage_blocked(
    async_db: AsyncSession, two_tenants: tuple[Organization, Organization]
):
    """Staff user of Org B cannot access Org A knowledge documents via HTTP endpoint."""
    org_a, org_b = two_tenants

    user_a = User(
        organization_id=org_a.id,
        email=f"author-a-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Password123!"),
        display_name="Author Alpha",
        role=UserRole.admin,
        is_active=True,
    )
    user_b = User(
        organization_id=org_b.id,
        email=f"author-b-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Password123!"),
        display_name="Author Beta",
        role=UserRole.admin,
        is_active=True,
    )
    async_db.add_all([user_a, user_b])
    await async_db.flush()

    doc_a = KnowledgeDocument(
        organization_id=org_a.id,
        title="Org A Secret Runbook",
        source_type="text",
        content="Internal infrastructure keys and procedures.",
        status=DocumentStatus.draft,
        ingestion_status=IngestionStatus.ready,
        created_by_user_id=user_a.id,
    )
    async_db.add(doc_a)
    await async_db.commit()

    token_a = create_access_jwt(
        subject=str(user_a.id),
        role=user_a.role.value,
        additional_claims={"org_id": str(org_a.id), "org_slug": org_a.slug},
    )
    token_b = create_access_jwt(
        subject=str(user_b.id),
        role=user_b.role.value,
        additional_claims={"org_id": str(org_b.id), "org_slug": org_b.slug},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Org A admin can access doc_a
        resp_a = await client.get(
            f"/api/v1/knowledge/documents/{doc_a.id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a.status_code == 200
        assert resp_a.json()["id"] == str(doc_a.id)

        # Org B admin receives 404
        resp_b = await client.get(
            f"/api/v1/knowledge/documents/{doc_a.id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.status_code == 404

        # Org B listing documents does not see doc_a
        resp_b_list = await client.get(
            "/api/v1/knowledge/documents",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b_list.status_code == 200
        doc_ids = [item["id"] for item in resp_b_list.json()["items"]]
        assert str(doc_a.id) not in doc_ids

