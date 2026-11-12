"""Tests for Sub-phase 5A.1: Customer Identity Foundation & Organization Context."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.core.context import OrganizationContext, get_organization_context
from app.database import async_session_factory
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation, ConversationStatus
from app.models.visitor_session import VisitorSession
from app.security.customer_auth import (
    CustomerTokenAlgorithmError,
    CustomerTokenClaimsError,
    CustomerTokenExpiredError,
    CustomerTokenInvalidError,
    CustomerTokenVerifier,
    create_customer_jwt,
)


@pytest.fixture
def rsa_keypair():
    """Generate ephemeral RSA 2048 keypair for asymmetric token testing."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


@pytest.mark.asyncio
async def test_valid_customer_jwt_establishes_ial2():
    """Verify presenting valid customer token creates an IAL 2 session with verified_customer_id."""
    token = create_customer_jwt(customer_id="cust_1001", email="alice@hostportal.com")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "Origin": "http://localhost:5174",
                "x-customer-token": token,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_assurance_level"] == 2
        assert data["verified_customer_id"] == "cust_1001"
        assert "session_token" in data
        assert data.get("visitor_token") == data["session_token"]
        # Raw token must NEVER appear in response payload
        assert token not in str(data)


@pytest.mark.asyncio
async def test_tampered_jwt_rejected_and_falls_back_to_ial0():
    """Verify tampered customer token fails verification and safely falls back to IAL 0."""
    valid_token = create_customer_jwt(customer_id="cust_1001")
    # Tamper with the payload portion
    parts = valid_token.split(".")
    tampered_token = f"{parts[0]}.eyJhZG1pbiI6dHJ1ZX0.{parts[2]}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "Origin": "http://localhost:5174",
                "x-customer-token": tampered_token,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Must safely fall back to anonymous IAL 0
        assert data["identity_assurance_level"] == 0
        assert data["verified_customer_id"] is None
        assert "session_token" in data


@pytest.mark.asyncio
async def test_expired_jwt_rejected():
    """Verify expired token is rejected by verifier and falls back to IAL 0 on session init."""
    expired_token = create_customer_jwt(
        customer_id="cust_expired",
        expires_in_minutes=-5,  # 5 minutes in the past
    )

    verifier = CustomerTokenVerifier()
    with pytest.raises(CustomerTokenExpiredError):
        verifier.verify_token(expired_token)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "Origin": "http://localhost:5174",
                "x-customer-token": expired_token,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_assurance_level"] == 0
        assert data["verified_customer_id"] is None


@pytest.mark.asyncio
async def test_jwt_none_algorithm_rejected():
    """Verify tokens requesting 'none' algorithm are rejected unconditionally."""
    header = {"alg": "none", "typ": "JWT"}
    payload = {
        "sub": "cust_attacker",
        "iss": settings.CUSTOMER_JWT_ISSUER,
        "aud": settings.CUSTOMER_JWT_AUDIENCE,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    # Manually craft none algorithm token
    import base64
    import json

    h_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    p_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    none_token = f"{h_b64}.{p_b64}."

    verifier = CustomerTokenVerifier()
    with pytest.raises(CustomerTokenAlgorithmError):
        verifier.verify_token(none_token)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "Origin": "http://localhost:5174",
                "x-customer-token": none_token,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_assurance_level"] == 0
        assert data["verified_customer_id"] is None


@pytest.mark.asyncio
async def test_jwt_wrong_algorithm_rejected():
    """Verify token signed with disallowed algorithm is rejected."""
    token = jwt.encode(
        {
            "sub": "cust_alg_mismatch",
            "iss": settings.CUSTOMER_JWT_ISSUER,
            "aud": settings.CUSTOMER_JWT_AUDIENCE,
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        },
        "this-is-a-very-long-test-secret-key-that-exceeds-64-bytes-for-sha512-testing!!",
        algorithm="HS512",
    )

    verifier = CustomerTokenVerifier(allowed_algorithms=["HS256"])
    with pytest.raises(CustomerTokenAlgorithmError):
        verifier.verify_token(token)


@pytest.mark.asyncio
async def test_jwt_wrong_issuer_rejected():
    """Verify token from untrusted issuer is rejected."""
    token = create_customer_jwt(
        customer_id="cust_wrong_iss",
        issuer="untrusted-portal",
    )
    verifier = CustomerTokenVerifier()
    with pytest.raises(CustomerTokenClaimsError):
        verifier.verify_token(token)


@pytest.mark.asyncio
async def test_jwt_wrong_audience_rejected():
    """Verify token intended for different audience is rejected."""
    token = create_customer_jwt(
        customer_id="cust_wrong_aud",
        audience="another-service",
    )
    verifier = CustomerTokenVerifier()
    with pytest.raises(CustomerTokenClaimsError):
        verifier.verify_token(token)


@pytest.mark.asyncio
async def test_jwt_forged_signature_rejected():
    """Verify token signed with forged key is rejected."""
    token = create_customer_jwt(
        customer_id="cust_forged",
        secret="attacker-secret-key-at-least-32-chars-long",
    )
    verifier = CustomerTokenVerifier()
    with pytest.raises(CustomerTokenInvalidError):
        verifier.verify_token(token)


@pytest.mark.asyncio
async def test_raw_jwt_never_persisted_in_database():
    """Verify raw JWT is NEVER stored in database, only SHA-256 digest is stored."""
    token = create_customer_jwt(customer_id="cust_privacy_check")
    expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "Origin": "http://localhost:5174",
                "x-customer-token": token,
            },
        )
        assert resp.status_code == 200
        sess_token = resp.json()["session_token"]

    async with async_session_factory() as session:
        sess_stmt = select(VisitorSession).where(
            VisitorSession.token_hash == hashlib.sha256(sess_token.encode("utf-8")).hexdigest()
        )
        res = await session.execute(sess_stmt)
        db_sess = res.scalar_one()

        assert db_sess.customer_token_hash == expected_hash
        assert db_sess.verified_customer_id == "cust_privacy_check"
        assert db_sess.identity_assurance_level == 2

        # Check audit event
        audit_stmt = select(AuditEvent).where(
            AuditEvent.resource_id == db_sess.id,
            AuditEvent.event_type == "visitor.session.created",
        )
        audit_res = await session.execute(audit_stmt)
        db_audit = audit_res.scalar_one()
        audit_str = str(db_audit.metadata_json)
        assert token not in audit_str
        assert expected_hash[:8] in audit_str


@pytest.mark.asyncio
async def test_anonymous_session_remains_ial0():
    """Verify anonymous session creation sets IAL 0 and verified_customer_id is None."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_assurance_level"] == 0
        assert data["verified_customer_id"] is None


@pytest.mark.asyncio
async def test_session_renewal_same_customer():
    """Verify resuming session with same customer token preserves conversation."""
    token = create_customer_jwt(customer_id="cust_same")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create
        resp1 = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174", "x-customer-token": token},
        )
        data1 = resp1.json()
        sess_token = data1["session_token"]
        conv_id = data1["conversation_id"]

        # Resume
        resp2 = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "Origin": "http://localhost:5174",
                "Authorization": f"Bearer {sess_token}",
                "x-customer-token": token,
            },
        )
        data2 = resp2.json()
        assert data2["conversation_id"] == conv_id
        assert data2["identity_assurance_level"] == 2
        assert data2["verified_customer_id"] == "cust_same"


@pytest.mark.asyncio
async def test_anonymous_cannot_inherit_verified_session():
    """Verify anonymous request cannot resume previously verified session (drops to new session)."""
    cust_token = create_customer_jwt(customer_id="cust_secret_user")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Establish verified session
        resp1 = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174", "x-customer-token": cust_token},
        )
        data1 = resp1.json()
        sess_token = data1["session_token"]
        conv1_id = data1["conversation_id"]

        # 2. Reconnect presenting session token WITHOUT customer token
        resp2 = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "Origin": "http://localhost:5174",
                "Authorization": f"Bearer {sess_token}",
            },
        )
        data2 = resp2.json()
        # Must establish brand-new anonymous session and NOT inherit conv1
        assert data2["identity_assurance_level"] == 0
        assert data2["verified_customer_id"] is None
        assert data2["conversation_id"] != conv1_id

    # Verify conv1 was closed
    async with async_session_factory() as session:
        c_res = await session.execute(
            select(Conversation).where(Conversation.id == uuid.UUID(conv1_id))
        )
        c = c_res.scalar_one()
        assert c.status == ConversationStatus.closed


@pytest.mark.asyncio
async def test_account_switching_invalidates_previous_session():
    """Verify switching customer token revokes old session and creates fresh conversation."""
    token_a = create_customer_jwt(customer_id="cust_alice")
    token_b = create_customer_jwt(customer_id="cust_bob")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Alice connects
        resp1 = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174", "x-customer-token": token_a},
        )
        data1 = resp1.json()
        sess_token = data1["session_token"]
        conv_a_id = data1["conversation_id"]

        # 2. Presenting Alice's session token but Bob's customer token
        resp2 = await client.post(
            "/api/v1/widget/sessions",
            headers={
                "Origin": "http://localhost:5174",
                "Authorization": f"Bearer {sess_token}",
                "x-customer-token": token_b,
            },
        )
        data2 = resp2.json()
        assert data2["identity_assurance_level"] == 2
        assert data2["verified_customer_id"] == "cust_bob"
        assert data2["conversation_id"] != conv_a_id

    # Verify Alice's conversation was closed
    async with async_session_factory() as session:
        c_res = await session.execute(
            select(Conversation).where(Conversation.id == uuid.UUID(conv_a_id))
        )
        c = c_res.scalar_one()
        assert c.status == ConversationStatus.closed


@pytest.mark.asyncio
async def test_session_logout_invalidates_session_and_closes_conversation():
    """Verify logout revokes session and closes active conversation."""
    token = create_customer_jwt(customer_id="cust_logout_user")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Connect
        resp1 = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174", "x-customer-token": token},
        )
        sess_token = resp1.json()["session_token"]
        conv_id = resp1.json()["conversation_id"]

        # 2. Call logout
        logout_resp = await client.post(
            "/api/v1/widget/sessions/logout",
            headers={
                "Origin": "http://localhost:5174",
                "Authorization": f"Bearer {sess_token}",
            },
        )
        assert logout_resp.status_code == 200
        assert logout_resp.json()["status"] == "ok"

        # 3. Trying to access messages with revoked session returns 401
        msg_resp = await client.get(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {sess_token}"},
        )
        assert msg_resp.status_code == 401

    # 4. Verify conversation is closed in database
    async with async_session_factory() as db:
        c_res = await db.execute(
            select(Conversation).where(Conversation.id == uuid.UUID(conv_id))
        )
        c = c_res.scalar_one()
        assert c.status == ConversationStatus.closed


@pytest.mark.asyncio
async def test_customer_token_expiration_drops_ial():
    """Verify VisitorSession.effective_identity_assurance_level drops to 0 when token expires."""
    now = datetime.now(UTC)
    session = VisitorSession(
        id=uuid.uuid4(),
        visitor_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        token_hash="sample_hash",
        origin="http://localhost:5174",
        expires_at=now + timedelta(hours=24),
        verified_customer_id="cust_test",
        customer_token_hash="cust_hash",
        identity_assurance_level=2,
        customer_token_expires_at=now - timedelta(seconds=1),  # Expired
    )
    assert session.is_customer_authenticated is False
    assert session.effective_identity_assurance_level == 0


def test_organization_context_injection():
    """Verify OrganizationContext provides explicit server-side organization boundary."""
    ctx = get_organization_context()
    assert isinstance(ctx, OrganizationContext)
    assert ctx.organization_id == settings.ORGANIZATION_ID
    assert ctx.organization_name == settings.ORGANIZATION_NAME
    assert ctx.is_active is True


@pytest.mark.asyncio
async def test_chat_supplied_identity_does_not_authorize():
    """Verify updating profile in chat text NEVER sets IAL 2 or verified_customer_id."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Anonymous session
        sess_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
        )
        sess_data = sess_resp.json()
        sess_token = sess_data["session_token"]

        # 2. Visitor supplies name and email via widget profile
        patch_resp = await client.patch(
            "/api/v1/widget/profile",
            headers={"Authorization": f"Bearer {sess_token}"},
            json={"name": "Attacker Impersonator", "email": "ceo@victimcompany.com"},
        )
        assert patch_resp.status_code == 200

    # 3. Check session in DB: MUST remain IAL 0 with no verified_customer_id
    token_hash = hashlib.sha256(sess_token.encode("utf-8")).hexdigest()
    async with async_session_factory() as db:
        s_res = await db.execute(
            select(VisitorSession).where(VisitorSession.token_hash == token_hash)
        )
        db_session = s_res.scalar_one()
        assert db_session.identity_assurance_level == 0
        assert db_session.verified_customer_id is None


@pytest.mark.asyncio
async def test_asymmetric_jwt_rs256_verification(rsa_keypair):
    """Verify asymmetric RS256 token verification with public key."""
    priv_pem, pub_pem = rsa_keypair
    token = create_customer_jwt(
        customer_id="cust_rsa_user",
        private_key=priv_pem,
        algorithm="RS256",
    )
    verifier = CustomerTokenVerifier(
        public_key=pub_pem,
        allowed_algorithms=["RS256"],
    )
    identity = verifier.verify_token(token)
    assert identity.customer_id == "cust_rsa_user"
    assert identity.identity_assurance_level == 2


@pytest.mark.asyncio
async def test_backward_compatibility_anonymous_widget_conversations():
    """Verify existing Phase 0-4 anonymous conversation workflow is fully preserved."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create session
        sess_resp = await client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "http://localhost:5174"},
            json={"name": "Bob the Visitor"},
        )
        assert sess_resp.status_code == 200
        sess_data = sess_resp.json()
        assert sess_data["identity_assurance_level"] == 0
        sess_token = sess_data["session_token"]
        conv_id = sess_data["conversation_id"]
        assert conv_id is not None

        # Post message
        msg_resp = await client.post(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {sess_token}"},
            json={"content": "What are your business hours?", "client_message_id": str(uuid.uuid4())},
        )
        assert msg_resp.status_code == 201

        # Read messages
        list_resp = await client.get(
            "/api/v1/widget/messages",
            headers={"Authorization": f"Bearer {sess_token}"},
        )
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        assert len(items) >= 1
        assert items[0]["content"] == "What are your business hours?"


def test_demo_showcase_jwt_verification():
    verifier = CustomerTokenVerifier()
    identity = verifier.verify_token("demo_jwt_cust_bank_401")
    assert identity.customer_id == "cust_bank_401"
    assert identity.identity_assurance_level == 2
    assert identity.email == "cust_bank_401@showcase.demo"

