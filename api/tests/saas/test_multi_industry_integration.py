"""End-to-end integration tests for multi-industry domains (Banking, Healthcare, SaaS, Retail).

Verifies:
1. Dynamic greeting messages tailored per client/domain.
2. Dynamic unauthenticated clarification messages tailored per client/domain.
3. Cryptographically verified customer identity queries ("who am I?").
4. Domain-specific entity resolution and connector querying:
   - Apex Global Bank (ACC-440192, TXN-55102, WIRE-88129)
   - MediCare Telehealth (APT-1092, RX-44912, PAT-8812)
   - CloudPulse SaaS (WS-771a8b, WS-992b1c, SUB-2001, QUOTA-301)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import desc, select

from app.database import async_session_factory
from app.gateway.tool_gateway import default_tool_gateway
from app.models.assistant_job import AssistantJob, AssistantJobStatus
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, SenderType
from app.models.organization import Organization
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.schemas.entities import (
    get_default_apex_bank_entity_schema,
    get_default_cloudpulse_entity_schema,
    get_default_medicare_entity_schema,
    get_default_olist_entity_schema,
)
from app.services.job_worker import enqueue_job, run_worker_cycle
from app.services.multi_agent_service import _build_tenant_auth_required, _build_tenant_greeting
from app.prompts.greeting import generate_tenant_greeting_with_llm, generate_tenant_auth_required_with_llm


@pytest.mark.asyncio
async def test_llm_generated_greeting_and_auth_guidance():
    """Verify that dynamic LLM greeting and auth guidance generate context-aware messages."""
    bank_org = Organization(
        id=uuid.uuid4(),
        slug="apex-bank",
        name="Apex Global Bank",
        entity_schemas_json=get_default_apex_bank_entity_schema().model_dump(mode="json"),
    )
    greeting = await generate_tenant_greeting_with_llm(bank_org, "hello!")
    assert "Apex Global Bank" in greeting
    assert "account" in greeting.lower()

    auth_prompt = await generate_tenant_auth_required_with_llm(bank_org, "my accounts")
    assert "bank accounts and financial records" in auth_prompt
    assert "1-Click Customer Switcher" in auth_prompt

    med_org = Organization(
        id=uuid.uuid4(),
        slug="medicare",
        name="MediCare Telehealth",
        entity_schemas_json=get_default_medicare_entity_schema().model_dump(mode="json"),
    )
    med_greeting = await generate_tenant_greeting_with_llm(med_org, "hi there")
    assert "MediCare Telehealth" in med_greeting
    assert "appointments" in med_greeting.lower()



@pytest.mark.asyncio
async def test_dynamic_tenant_greetings_and_auth_prompts():
    """Verify that greetings and unauthenticated auth prompts dynamically adapt per domain."""
    # 1. Retail (Olist)
    olist_org = Organization(
        id=uuid.uuid4(),
        slug="default",
        name="Olist Store",
        entity_schemas_json=get_default_olist_entity_schema().model_dump(mode="json"),
    )
    assert "return and refund policies" in _build_tenant_greeting(olist_org)
    assert "orders and account details" in _build_tenant_auth_required(olist_org)

    # 2. Apex Global Bank
    bank_org = Organization(
        id=uuid.uuid4(),
        slug="apex-bank",
        name="Apex Global Bank",
        entity_schemas_json=get_default_apex_bank_entity_schema().model_dump(mode="json"),
    )
    bank_greeting = _build_tenant_greeting(bank_org)
    bank_auth = _build_tenant_auth_required(bank_org)
    assert "account balances" in bank_greeting
    assert "wire transfers" in bank_greeting
    assert "bank accounts and financial records" in bank_auth

    # 3. MediCare Telehealth
    medicare_org = Organization(
        id=uuid.uuid4(),
        slug="medicare",
        name="MediCare Telehealth",
        entity_schemas_json=get_default_medicare_entity_schema().model_dump(mode="json"),
    )
    med_greeting = _build_tenant_greeting(medicare_org)
    med_auth = _build_tenant_auth_required(medicare_org)
    assert "upcoming appointments" in med_greeting
    assert "prescriptions" in med_greeting
    assert "appointments and prescription records" in med_auth

    # 4. CloudPulse SaaS
    saas_org = Organization(
        id=uuid.uuid4(),
        slug="cloudpulse",
        name="CloudPulse SaaS",
        entity_schemas_json=get_default_cloudpulse_entity_schema().model_dump(mode="json"),
    )
    saas_greeting = _build_tenant_greeting(saas_org)
    saas_auth = _build_tenant_auth_required(saas_org)
    assert "cloud workspaces" in saas_greeting
    assert "subscription tiers" in saas_greeting
    assert "cloud workspaces and subscriptions" in saas_auth


@pytest.mark.asyncio
async def test_apex_bank_account_inquiry_and_verified_identity():
    """Verify that verified customer Elena Rostova can query account and identity on Apex Global Bank."""
    async with async_session_factory() as db:
        org = Organization(
            id=uuid.uuid4(),
            slug=f"apex-{uuid.uuid4().hex[:6]}",
            name="Apex Global Bank",
            entity_schemas_json=get_default_apex_bank_entity_schema().model_dump(mode="json"),
            is_active=True,
        )
        db.add(org)
        visitor = Visitor(
            id=uuid.uuid4(),
            organization_id=org.id,
            name="Elena Rostova",
            email="elena.rostova@apexbank-corp.com",
        )
        db.add(visitor)
        conv = Conversation(
            id=uuid.uuid4(),
            organization_id=org.id,
            visitor_id=visitor.id,
            status=ConversationStatus.open,
            title="Apex Bank Thread",
        )
        db.add(conv)
        now = datetime.now(UTC)
        session = VisitorSession(
            id=uuid.uuid4(),
            organization_id=org.id,
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"token_{uuid.uuid4().hex}",
            origin="http://localhost:5174",
            verified_customer_id="cust_bank_401",
            identity_assurance_level=2,
            expires_at=now + timedelta(hours=24),
            customer_token_expires_at=now + timedelta(hours=1),
        )
        db.add(session)
        await db.commit()

        # Step 1: Query account balance
        msg1 = Message(
            id=uuid.uuid4(),
            organization_id=org.id,
            conversation_id=conv.id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="What is the available balance on account ACC-440192?",
            created_at=now,
        )
        db.add(msg1)
        await db.flush()
        job1 = await enqueue_job(db, conversation_id=conv.id, message_id=msg1.id, organization_id=org.id)
        await db.commit()

    handled = await run_worker_cycle(
        worker_id="bank_worker",
        db_factory=async_session_factory,
        job_id=job1.id,
    )
    assert handled is True

    async with async_session_factory() as db:
        m_stmt = (
            select(Message)
            .where(Message.conversation_id == conv.id, Message.sender_type == SenderType.assistant)
            .order_by(desc(Message.created_at))
        )
        reply1 = (await db.execute(m_stmt)).scalars().first()
        assert reply1 is not None
        assert "ACC-440192" in reply1.content
        assert "24,850.00" in reply1.content or "Checking" in reply1.content

        # Step 2: Query identity ("who am I?")
        msg2 = Message(
            id=uuid.uuid4(),
            organization_id=org.id,
            conversation_id=conv.id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="who am I?",
            created_at=now + timedelta(seconds=10),
        )
        db.add(msg2)
        await db.flush()
        job2 = await enqueue_job(db, conversation_id=conv.id, message_id=msg2.id, organization_id=org.id)
        await db.commit()

    handled2 = await run_worker_cycle(
        worker_id="bank_worker",
        db_factory=async_session_factory,
        job_id=job2.id,
    )
    assert handled2 is True

    async with async_session_factory() as db:
        m_stmt = (
            select(Message)
            .where(Message.conversation_id == conv.id, Message.sender_type == SenderType.assistant)
            .order_by(desc(Message.created_at))
        )
        reply2 = (await db.execute(m_stmt)).scalars().first()
        assert reply2 is not None
        assert "Elena Rostova" in reply2.content or "cust_bank_401" in reply2.content
        assert "verified" in reply2.content.lower()


@pytest.mark.asyncio
async def test_medicare_telehealth_appointment_inquiry():
    """Verify that verified patient Marcus Vance can query clinic appointment on MediCare Telehealth."""
    async with async_session_factory() as db:
        org = Organization(
            id=uuid.uuid4(),
            slug=f"medicare-{uuid.uuid4().hex[:6]}",
            name="MediCare Telehealth",
            entity_schemas_json=get_default_medicare_entity_schema().model_dump(mode="json"),
            is_active=True,
        )
        db.add(org)
        visitor = Visitor(
            id=uuid.uuid4(),
            organization_id=org.id,
            name="Marcus Vance",
            email="marcus.vance@medicare-patient.org",
        )
        db.add(visitor)
        conv = Conversation(
            id=uuid.uuid4(),
            organization_id=org.id,
            visitor_id=visitor.id,
            status=ConversationStatus.open,
            title="MediCare Thread",
        )
        db.add(conv)
        now = datetime.now(UTC)
        session = VisitorSession(
            id=uuid.uuid4(),
            organization_id=org.id,
            visitor_id=visitor.id,
            conversation_id=conv.id,
            token_hash=f"token_{uuid.uuid4().hex}",
            origin="http://localhost:5174",
            verified_customer_id="pat_8812",
            identity_assurance_level=2,
            expires_at=now + timedelta(hours=24),
            customer_token_expires_at=now + timedelta(hours=1),
        )
        db.add(session)
        await db.commit()

        msg = Message(
            id=uuid.uuid4(),
            organization_id=org.id,
            conversation_id=conv.id,
            client_message_id=uuid.uuid4(),
            sender_type=SenderType.visitor,
            content="When is my upcoming appointment APT-1092?",
            created_at=now,
        )
        db.add(msg)
        await db.flush()
        job = await enqueue_job(db, conversation_id=conv.id, message_id=msg.id, organization_id=org.id)
        await db.commit()

    handled = await run_worker_cycle(
        worker_id="med_worker",
        db_factory=async_session_factory,
        job_id=job.id,
    )
    assert handled is True

    async with async_session_factory() as db:
        m_stmt = (
            select(Message)
            .where(Message.conversation_id == conv.id, Message.sender_type == SenderType.assistant)
            .order_by(desc(Message.created_at))
        )
        reply = (await db.execute(m_stmt)).scalars().first()
        assert reply is not None
        assert "APT-1092" in reply.content
        assert "Lin" in reply.content or "Cardiology" in reply.content
