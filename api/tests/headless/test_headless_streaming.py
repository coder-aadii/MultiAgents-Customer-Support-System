import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import async_session_factory
from app.main import app
from app.models.organization import Organization
from app.models.visitor_session import VisitorSession
from app.security.customer_auth import create_customer_jwt
from app.security.tokens import hash_token
from app.tenancy.context import TenantContext


@pytest.mark.asyncio
class TestHeadlessApi:
    async def test_anonymous_session_exchange(self):
        org_id = uuid.uuid4()
        async with async_session_factory() as db_session:
            org = Organization(
                id=org_id,
                slug=f"headless-{uuid.uuid4().hex[:8]}",
                name="Headless Test Org",
                is_active=True,
            )
            db_session.add(org)
            await db_session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v2/auth/customer-session-exchange",
                headers={"X-Organization-Slug": org.slug},
                json={"channel": "mobile_ios", "device_fingerprint": "fp_ios_123"},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["identity_assurance_level"] == 0
            assert data["verified_customer_id"] is None
            assert data["session_token"].startswith("mcss_sess_")
            assert data["active_conversation_id"] is not None

    async def test_verified_customer_session_exchange(self):
        org_id = uuid.uuid4()
        async with async_session_factory() as db_session:
            org = Organization(
                id=org_id,
                slug=f"headless-{uuid.uuid4().hex[:8]}",
                name="Headless Verified Org",
                is_active=True,
            )
            db_session.add(org)
            await db_session.commit()

        # Generate customer token
        token = create_customer_jwt(
            customer_id="cust_headless_99",
            email="developer@company.io",
            custom_claims={"org_id": str(org_id)},
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v2/auth/customer-session-exchange",
                headers={"X-Organization-Slug": org.slug},
                json={
                    "customer_identity_token": token,
                    "channel": "api",
                    "device_fingerprint": "fp_api_99",
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert data["identity_assurance_level"] == 2
            assert data["verified_customer_id"] == "cust_headless_99"
            assert data["session_token"].startswith("mcss_sess_")
            active_conv_id = data["active_conversation_id"]

            session_token = data["session_token"]
            auth_headers = {"Authorization": f"Bearer {session_token}"}

            # Get conversation metadata
            res_conv = await client.get(
                f"/api/v2/headless/conversations/{active_conv_id}",
                headers=auth_headers,
            )
            assert res_conv.status_code == 200
            assert res_conv.json()["id"] == active_conv_id

            # Post a streaming message
            res_stream = await client.post(
                f"/api/v2/headless/conversations/{active_conv_id}/messages/stream",
                headers=auth_headers,
                json={"content": "Hello headless support!"},
            )
            assert res_stream.status_code == 200
            assert "text/event-stream" in res_stream.headers.get("content-type", "")

            stream_text = res_stream.text
            assert "event: message_received" in stream_text
            assert "event: planning" in stream_text
            assert "event: completed" in stream_text

            # Get conversation messages history
            res_messages = await client.get(
                f"/api/v2/headless/conversations/{active_conv_id}/messages",
                headers=auth_headers,
            )
            assert res_messages.status_code == 200
            messages = res_messages.json()
            assert len(messages) >= 1
            assert any(m["content"] == "Hello headless support!" for m in messages)

    async def test_session_exchange_rejects_foreign_organization_token(self):
        org1_id = uuid.uuid4()
        org2_id = uuid.uuid4()
        async with async_session_factory() as db_session:
            org1 = Organization(id=org1_id, slug=f"org1-{uuid.uuid4().hex[:6]}", name="Org 1", is_active=True)
            org2 = Organization(id=org2_id, slug=f"org2-{uuid.uuid4().hex[:6]}", name="Org 2", is_active=True)
            db_session.add_all([org1, org2])
            await db_session.commit()

        # Token created for org1
        token = create_customer_jwt(
            customer_id="cust_foreign",
            email="user@org1.com",
            custom_claims={"org_id": str(org1_id)},
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Attempt to use token against org2
            res = await client.post(
                "/api/v2/auth/customer-session-exchange",
                headers={"X-Organization-Slug": org2.slug},
                json={"customer_identity_token": token},
            )
            assert res.status_code == 403
            assert "different organization" in res.text

    async def test_stream_enforces_conversation_ownership(self):
        org_id = uuid.uuid4()
        async with async_session_factory() as db_session:
            org = Organization(id=org_id, slug=f"org-{uuid.uuid4().hex[:6]}", name="Org Test", is_active=True)
            db_session.add(org)
            await db_session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v2/auth/customer-session-exchange",
                headers={"X-Organization-Slug": org.slug},
                json={"channel": "web_widget"},
            )
            assert res.status_code == 200
            session_token = res.json()["session_token"]
            other_conv_id = uuid.uuid4()

            # Attempt to stream on an unauthorized / foreign conversation ID
            res_tamper = await client.post(
                f"/api/v2/headless/conversations/{other_conv_id}/messages/stream",
                headers={"Authorization": f"Bearer {session_token}"},
                json={"content": "Trying to snoop"},
            )
            assert res_tamper.status_code == 404
