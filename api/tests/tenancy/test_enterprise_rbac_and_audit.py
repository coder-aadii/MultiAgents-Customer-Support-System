from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.errors import ForbiddenError
from app.database import async_session_factory
from app.main import app
from app.models.audit_event import ActorType, AuditEvent
from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.models.user import User, UserRole
from app.security.password import hash_password
from app.security.tokens import create_access_jwt
from app.tenancy.rbac import (
    DEFAULT_ROLE_PERMISSIONS,
    PERM_ACTIONS_APPROVE,
    PERM_AUDIT_EXPORT,
    PERM_AUDIT_READ,
    PERM_CONVERSATIONS_READ,
    PERM_CONVERSATIONS_WRITE,
    require_permission,
)


@pytest.mark.asyncio
async def test_rbac_default_permissions_matrix():
    """Verify standard enterprise role-to-permission mappings."""
    admin_perms = DEFAULT_ROLE_PERMISSIONS[OrgMemberRole.admin.value]
    assert "*" in admin_perms

    supervisor_perms = DEFAULT_ROLE_PERMISSIONS[OrgMemberRole.supervisor.value]
    assert PERM_CONVERSATIONS_READ in supervisor_perms
    assert PERM_CONVERSATIONS_WRITE in supervisor_perms
    assert PERM_ACTIONS_APPROVE in supervisor_perms
    assert PERM_AUDIT_READ in supervisor_perms
    assert PERM_AUDIT_EXPORT not in supervisor_perms

    agent_perms = DEFAULT_ROLE_PERMISSIONS[OrgMemberRole.agent.value]
    assert PERM_CONVERSATIONS_READ in agent_perms
    assert PERM_CONVERSATIONS_WRITE in agent_perms
    assert PERM_ACTIONS_APPROVE not in agent_perms
    assert PERM_AUDIT_EXPORT not in agent_perms

    auditor_perms = DEFAULT_ROLE_PERMISSIONS[OrgMemberRole.auditor.value]
    assert PERM_AUDIT_READ in auditor_perms
    assert PERM_AUDIT_EXPORT in auditor_perms
    assert PERM_CONVERSATIONS_READ in auditor_perms
    assert PERM_CONVERSATIONS_WRITE not in auditor_perms
    assert PERM_ACTIONS_APPROVE not in auditor_perms


@pytest.mark.asyncio
async def test_auditor_and_agent_rbac_enforcement_and_audit_export():
    """Verify that auditor role can export audit records but agent cannot, and check CSV/JSON formats."""
    async with async_session_factory() as db:
        # 1. Create Organization
        org = Organization(
            id=uuid.uuid4(),
            name="Compliance Corp",
            slug=f"comp-{uuid.uuid4().hex[:6]}",
        )
        db.add(org)
        await db.flush()

        # 2. Create Users: Auditor and Agent
        auditor_user = User(
            id=uuid.uuid4(),
            organization_id=org.id,
            email=f"auditor_{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("Pass123!"),
            display_name="Auditor User",
            role=UserRole.staff,
            is_active=True,
        )
        agent_user = User(
            id=uuid.uuid4(),
            organization_id=org.id,
            email=f"agent_{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("Pass123!"),
            display_name="Agent User",
            role=UserRole.staff,
            is_active=True,
        )
        db.add_all([auditor_user, agent_user])
        await db.flush()

        # 3. Create Memberships
        auditor_mem = OrganizationMembership(
            organization_id=org.id,
            user_id=auditor_user.id,
            role=OrgMemberRole.auditor.value,
            permissions=[],
        )
        agent_mem = OrganizationMembership(
            organization_id=org.id,
            user_id=agent_user.id,
            role=OrgMemberRole.agent.value,
            permissions=[],
        )
        db.add_all([auditor_mem, agent_mem])

        # 4. Seed Audit Events
        ev1 = AuditEvent(
            id=uuid.uuid4(),
            organization_id=org.id,
            actor_type=ActorType.user,
            actor_id=auditor_user.id,
            event_type="security.audit_reviewed",
            resource_type="audit_log",
            resource_id=uuid.uuid4(),
            metadata_json={"action": "review", "scope": "full"},
            created_at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        )
        ev2 = AuditEvent(
            id=uuid.uuid4(),
            organization_id=org.id,
            actor_type=ActorType.system,
            actor_id=None,
            event_type="action.executed",
            resource_type="order",
            resource_id=uuid.uuid4(),
            metadata_json={"action": "refund", "amount": 99.50},
            created_at=datetime(2026, 3, 1, 13, 0, tzinfo=UTC),
        )
        db.add_all([ev1, ev2])
        await db.commit()

        auditor_token = create_access_jwt(
            subject=str(auditor_user.id),
            role="staff",
            org_id=str(org.id),
        )
        agent_token = create_access_jwt(
            subject=str(agent_user.id),
            role="staff",
            org_id=str(org.id),
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # A. Auditor can read audit events
        res_list = await client.get(
            "/api/v1/audit/events",
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert res_list.status_code == 200, res_list.text
        data = res_list.json()
        assert len(data["items"]) == 2

        # B. Agent lacks PERM_AUDIT_EXPORT -> 403 Forbidden
        res_export_forbidden = await client.get(
            "/api/v1/audit/export?format=csv",
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert res_export_forbidden.status_code == 403
        assert "lacks grant" in res_export_forbidden.text

        # C. Auditor has PERM_AUDIT_EXPORT -> 200 OK CSV
        res_csv = await client.get(
            "/api/v1/audit/export?format=csv",
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert res_csv.status_code == 200
        assert res_csv.headers["content-type"] == "text/csv; charset=utf-8"
        csv_body = res_csv.text
        assert "Timestamp (UTC),Event ID,Actor Type,Actor ID,Event Type" in csv_body
        assert "security.audit_reviewed" in csv_body
        assert "action.executed" in csv_body

        # D. Auditor exports JSON
        res_json = await client.get(
            "/api/v1/audit/export?format=json",
            headers={"Authorization": f"Bearer {auditor_token}"},
        )
        assert res_json.status_code == 200
        assert res_json.headers["content-type"] == "application/json"
        exported_events = res_json.json()
        assert isinstance(exported_events, list)
        assert len(exported_events) == 2
        event_types = {e["event_type"] for e in exported_events}
        assert event_types == {"security.audit_reviewed", "action.executed"}


@pytest.mark.asyncio
async def test_audit_csv_export_neutralizes_formula_injection():
    """Verify that malicious audit fields starting with =, +, -, @ are escaped in CSV output."""
    async with async_session_factory() as db:
        org = Organization(
            id=uuid.uuid4(),
            name="Formula Org",
            slug=f"formula-{uuid.uuid4().hex[:6]}",
        )
        db.add(org)
        await db.flush()

        auditor = User(
            id=uuid.uuid4(),
            organization_id=org.id,
            email=f"auditor_{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("Pass123!"),
            display_name="Auditor",
            role=UserRole.staff,
            is_active=True,
        )
        db.add(auditor)
        await db.flush()

        mem = OrganizationMembership(
            organization_id=org.id,
            user_id=auditor.id,
            role=OrgMemberRole.auditor.value,
            permissions=[],
        )
        db.add(mem)

        # Injected audit event with spreadsheet formula payload
        malicious_ev = AuditEvent(
            id=uuid.uuid4(),
            organization_id=org.id,
            actor_type=ActorType.visitor,
            actor_id=uuid.uuid4(),
            event_type="=cmd|' /C calc'!A0",
            resource_type="@SUM(1+1)",
            resource_id=uuid.uuid4(),
            metadata_json={"payload": "+cmd"},
            created_at=datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
        )
        db.add(malicious_ev)
        await db.commit()

        token = create_access_jwt(subject=str(auditor.id), role="staff", org_id=str(org.id))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/v1/audit/export?format=csv",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        csv_text = res.text
        # Formulas must be escaped with a leading single quote (')
        assert "'=cmd|' /C calc'!A0" in csv_text
        assert "'@SUM(1+1)" in csv_text


