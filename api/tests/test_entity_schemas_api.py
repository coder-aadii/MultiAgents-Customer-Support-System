import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import async_session_factory
from app.main import app
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.entities import get_default_olist_entity_schema
from app.security.password import hash_password
from app.security.tokens import create_access_jwt


@pytest.mark.asyncio
async def test_entity_schema_crud_and_validation():
    async with async_session_factory() as db:
        # Create a test organization
        org = Organization(
            id=uuid.uuid4(),
            name="SaaS Dev Org",
            slug=f"saas-org-{uuid.uuid4().hex[:6]}",
            entity_schemas_json={},
        )
        db.add(org)
        await db.flush()

        # Create admin user for this org
        admin = User(
            id=uuid.uuid4(),
            organization_id=org.id,
            email=f"admin_{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("AdminPass123!"),
            display_name="Org Admin",
            role=UserRole.admin,
            is_active=True,
        )
        db.add(admin)
        await db.commit()

        admin_token = create_access_jwt(str(admin.id), admin.role.value, org_id=str(org.id))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. GET default entities when empty
        resp = await client.get(f"/api/v1/organizations/{org.id}/entities", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "entities" in data
        entity_names = [e["name"] for e in data["entities"]]
        assert "order" in entity_names
        assert "customer" in entity_names

        # 2. POST invalid regex should fail
        invalid_payload = {
            "name": "invalid_entity",
            "display_name": "Invalid Entity",
            "identifier_pattern": "[a-z",  # unclosed bracket
        }
        resp = await client.post(f"/api/v1/organizations/{org.id}/entities", json=invalid_payload, headers=headers)
        assert resp.status_code == 422

        # 3. POST new custom entity (SaaS workspace)
        valid_workspace = {
            "name": "workspace",
            "display_name": "Cloud Workspace",
            "description": "Tenant team workspace",
            "identifier_pattern": r"\bWS-[0-9]{3,8}\b",
            "primary_key": "workspace_id",
            "capabilities": ["read", "upgrade"],
            "fields": [
                {"name": "workspace_id", "field_type": "string", "required": True, "is_identifier": True},
                {"name": "tier", "field_type": "string", "required": True},
            ],
        }
        resp = await client.post(f"/api/v1/organizations/{org.id}/entities", json=valid_workspace, headers=headers)
        assert resp.status_code == 201
        updated = resp.json()
        names = [e["name"] for e in updated["entities"]]
        assert "workspace" in names

        # 4. Verify entity persisted in DB
        async with async_session_factory() as db:
            refreshed = (await db.execute(select(Organization).where(Organization.id == org.id))).scalar_one()
            persisted_entities = refreshed.entity_schemas_json.get("entities", [])
            assert any(e["name"] == "workspace" for e in persisted_entities)

        # 5. DELETE entity
        del_resp = await client.delete(f"/api/v1/organizations/{org.id}/entities/workspace", headers=headers)
        assert del_resp.status_code == 200
        after_delete_names = [e["name"] for e in del_resp.json()["entities"]]
        assert "workspace" not in after_delete_names
