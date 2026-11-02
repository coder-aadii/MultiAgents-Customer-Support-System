from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models.conversation import Conversation, ConversationStatus
from app.models.csat_survey import CSATSurvey
from app.models.message import Message, SenderType
from app.models.organization import Organization
from app.models.visitor import Visitor
from app.operations.csat import CSATService
from app.operations.qa_audit import QAAuditor


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def csat_setup(async_db: AsyncSession):
    org = Organization(slug=f"csat-org-{uuid.uuid4().hex[:6]}", name="CSAT Org", is_active=True)
    async_db.add(org)
    await async_db.flush()

    visitor = Visitor(organization_id=org.id, name="Survey Customer")
    async_db.add(visitor)
    await async_db.flush()

    conv = Conversation(organization_id=org.id, visitor_id=visitor.id, status=ConversationStatus.closed)
    async_db.add(conv)
    await async_db.commit()

    return org, conv


@pytest.mark.asyncio
async def test_csat_survey_submission_with_signed_token(csat_setup):
    """CSAT survey verifies tamper-proof signed token and records rating."""
    org, conv = csat_setup
    token = CSATService.generate_survey_token(conv.id, "cust_verified_123")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Submit valid survey
        resp = await client.post(
            "/api/v1/csat/submit",
            json={"token": token, "rating": 5, "comment": "Support agent resolved my issue immediately!"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

        # Tampered token fails
        tampered_token = token[:-4] + "ffff"
        bad_resp = await client.post(
            "/api/v1/csat/submit",
            json={"token": tampered_token, "rating": 5},
        )
        assert bad_resp.status_code == 401

        # Duplicate submission rejected
        dup_resp = await client.post(
            "/api/v1/csat/submit",
            json={"token": token, "rating": 4},
        )
        assert dup_resp.status_code == 409


def test_qa_auditor_scores_conversation_compliance():
    """QAAuditor evaluates transcript and outputs rubric breakdown."""
    conv_id = uuid.uuid4()
    good_messages = [
        Message(
            conversation_id=conv_id,
            sender_type=SenderType.visitor,
            content="My order ORD-1234 hasn't arrived.",
        ),
        Message(
            conversation_id=conv_id,
            sender_type=SenderType.assistant,
            content="Hello! Thank you for contacting support. I am sorry for the delay on order ORD-1234. I understand how frustrating this is. Please let me look into this right now.",
        ),
        Message(
            conversation_id=conv_id,
            sender_type=SenderType.staff,
            content="I have verified tracking with the carrier. Your package will arrive tomorrow by 5 PM. Happy to help! Have a great day.",
        ),
    ]

    result = QAAuditor.audit_conversation(good_messages)
    assert result["passed"] is True
    assert result["overall_score"] >= 75.0
    assert result["rubrics"]["politeness"] > 10.0
    assert result["rubrics"]["empathy"] > 10.0
    assert result["rubrics"]["groundedness"] == 30.0
