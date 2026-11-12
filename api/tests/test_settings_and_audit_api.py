from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import async_session_factory
from app.main import app
from app.models.audit_event import ActorType, AuditEvent
from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.models.user import User, UserRole
from app.models.widget_settings import WidgetSettings
from app.security.password import hash_password
from app.security.tokens import create_access_jwt


@pytest.mark.asyncio
async def test_widget_settings_and_audit_security_lifecycle():
    async with async_session_factory() as db:
        # 1. Seed two isolated tenant organizations
        org_a = Organization(
            id=uuid.uuid4(),
            name="Org Alpha",
            slug=f"alpha-{uuid.uuid4().hex[:6]}",
        )
        org_b = Organization(
            id=uuid.uuid4(),
            name="Org Beta",
            slug=f"beta-{uuid.uuid4().hex[:6]}",
        )
        db.add_all([org_a, org_b])
        await db.flush()

        # 2. Seed Admin and Agent for Org A
        admin_a = User(
            id=uuid.uuid4(),
            organization_id=org_a.id,
            email=f"admin_{uuid.uuid4().hex[:6]}@alpha.example.com",
            password_hash=hash_password("Pass123!"),
            display_name="Alpha Admin",
            role=UserRole.staff,
            is_active=True,
        )
        agent_a = User(
            id=uuid.uuid4(),
            organization_id=org_a.id,
            email=f"agent_{uuid.uuid4().hex[:6]}@alpha.example.com",
            password_hash=hash_password("Pass123!"),
            display_name="Alpha Agent",
            role=UserRole.staff,
            is_active=True,
        )
        db.add_all([admin_a, agent_a])
        await db.flush()

        mem_admin_a = OrganizationMembership(
            organization_id=org_a.id,
            user_id=admin_a.id,
            role=OrgMemberRole.admin.value,
            permissions=["*"],
        )
        mem_agent_a = OrganizationMembership(
            organization_id=org_a.id,
            user_id=agent_a.id,
            role=OrgMemberRole.agent.value,
            permissions=["support:read"],
        )
        db.add_all([mem_admin_a, mem_agent_a])

        # 3. Seed Audit Events in Org A and Org B
        audit_a1 = AuditEvent(
            id=uuid.uuid4(),
            organization_id=org_a.id,
            actor_type=ActorType.user,
            actor_id=admin_a.id,
            event_type="security.login",
            resource_type="user",
            resource_id=admin_a.id,
            metadata_json={"action": "login"},
            created_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        )
        audit_a2 = AuditEvent(
            id=uuid.uuid4(),
            organization_id=org_a.id,
            actor_type=ActorType.user,
            actor_id=admin_a.id,
            event_type="settings.update",
            resource_type="widget",
            resource_id=uuid.uuid4(),
            metadata_json={"action": "update"},
            created_at=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
        )
        audit_b1 = AuditEvent(
            id=uuid.uuid4(),
            organization_id=org_b.id,
            actor_type=ActorType.user,
            actor_id=uuid.uuid4(),
            event_type="security.leak",
            resource_type="user",
            resource_id=uuid.uuid4(),
            metadata_json={"action": "login"},
            created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        )
        db.add_all([audit_a1, audit_a2, audit_b1])
        await db.commit()

        admin_a_token = create_access_jwt(str(admin_a.id), admin_a.role.value, org_id=str(org_a.id))
        agent_a_token = create_access_jwt(str(agent_a.id), agent_a.role.value, org_id=str(org_a.id))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test Case 1: Agent can read widget settings (default singleton auto-provisioned)
        resp = await client.get("/api/v1/settings/widget", headers={"Authorization": f"Bearer {agent_a_token}"})
        assert resp.status_code == 200
        settings_data = resp.json()
        assert settings_data["accent_color"] == "#2563eb"
        assert settings_data["allowed_origin"] == "http://localhost:5174"

        # Test Case 2: Agent (non-admin) CANNOT update widget settings (403 Forbidden)
        resp = await client.patch(
            "/api/v1/settings/widget",
            json={"accent_color": "#ff0000"},
            headers={"Authorization": f"Bearer {agent_a_token}"},
        )
        assert resp.status_code == 403

        # Test Case 3: Admin cannot set invalid origin like wildcard or javascript
        resp = await client.patch(
            "/api/v1/settings/widget",
            json={"allowed_origin": "*"},
            headers={"Authorization": f"Bearer {admin_a_token}"},
        )
        assert resp.status_code == 422

        resp = await client.patch(
            "/api/v1/settings/widget",
            json={"allowed_origin": "javascript:alert(1)"},
            headers={"Authorization": f"Bearer {admin_a_token}"},
        )
        assert resp.status_code == 422

        # Test Case 4: Admin cannot set invalid hex color
        resp = await client.patch(
            "/api/v1/settings/widget",
            json={"accent_color": "not-a-hex-code"},
            headers={"Authorization": f"Bearer {admin_a_token}"},
        )
        assert resp.status_code == 422

        # Test Case 5: Admin CAN update widget settings with valid values
        resp = await client.patch(
            "/api/v1/settings/widget",
            json={
                "allowed_origin": "https://alpha.acme.com",
                "accent_color": "#10b981",
                "welcome_message": "Welcome to Alpha Support!",
            },
            headers={"Authorization": f"Bearer {admin_a_token}"},
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["allowed_origin"] == "https://alpha.acme.com"
        assert updated["accent_color"] == "#10b981"
        assert updated["welcome_message"] == "Welcome to Alpha Support!"

        # Test Case 6: Audit log listing enforces tenant isolation (never returns org_b events)
        resp = await client.get("/api/v1/audit/events", headers={"Authorization": f"Bearer {admin_a_token}"})
        assert resp.status_code == 200
        audit_data = resp.json()
        assert "items" in audit_data
        event_types = [item["event_type"] for item in audit_data["items"]]
        assert "security.login" in event_types
        assert "settings.update" in event_types
        assert "security.leak" not in event_types  # From org_b, must be strictly excluded

        # Test Case 7: Audit log partial filter by event_type
        resp = await client.get(
            "/api/v1/audit/events?event_type=settings.update",
            headers={"Authorization": f"Bearer {admin_a_token}"},
        )
        assert resp.status_code == 200
        filtered = resp.json()
        assert len(filtered["items"]) == 1
        assert filtered["items"][0]["event_type"] == "settings.update"

        # Test Case 8: Substring/prefix partial match (e.g. 'sec' matches 'security.login')
        resp_partial = await client.get(
            "/api/v1/audit/events?event_type=sec",
            headers={"Authorization": f"Bearer {admin_a_token}"},
        )
        assert resp_partial.status_code == 200
        partial_items = resp_partial.json()["items"]
        assert len(partial_items) == 1
        assert partial_items[0]["event_type"] == "security.login"

        # Test Case 9: Partial resource_type match (e.g. 'wid' matches 'widget')
        resp_res = await client.get(
            "/api/v1/audit/events?resource_type=wid",
            headers={"Authorization": f"Bearer {admin_a_token}"},
        )
        assert resp_res.status_code == 200
        res_items = resp_res.json()["items"]
        assert len(res_items) == 1
        assert res_items[0]["resource_type"] == "widget"

