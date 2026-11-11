import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.connectors.rest.connector import RestEnterpriseConnector
from app.connectors.ssrf import SSRFProtectionGate, SSRFSecurityViolationError
from app.database import async_session_factory
from app.main import app
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.security.password import hash_password
from app.security.tokens import create_access_jwt
from app.tenancy.context import TenantContext


class TestSSRFProtectionGate:
    def test_blocks_loopback_and_localhost(self):
        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("http://127.0.0.1:8000/api")

        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("http://localhost:8000/api")

        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("http://127.0.0.2:9000")

    def test_blocks_cloud_metadata(self):
        # AWS / GCP / Azure metadata endpoint
        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("http://169.254.169.254/latest/meta-data/")

        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("http://169.254.1.1/info")

    def test_blocks_rfc_1918_private_subnets(self):
        # 10.0.0.0/8
        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("http://10.0.0.5:8080")

        # 172.16.0.0/12
        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("http://172.16.10.20:3000")

        # 192.168.0.0/16
        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("http://192.168.1.100")

    def test_blocks_carrier_grade_nat_and_broadcast(self):
        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("http://100.64.0.1:8000")

        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("http://0.0.0.0:8000")

    def test_allows_public_ip_and_schemes(self):
        # Public IP
        url = SSRFProtectionGate.validate_target_url("https://8.8.8.8/dns-query")
        assert url == "https://8.8.8.8/dns-query"

    def test_rejects_insecure_or_non_http_schemes(self):
        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("ftp://example.com/file")

        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("file:///etc/passwd")

    def test_enforce_https(self):
        with pytest.raises(SSRFSecurityViolationError):
            SSRFProtectionGate.validate_target_url("http://8.8.8.8", enforce_https=True)

    @pytest.mark.asyncio
    async def test_rest_connector_enforces_ssrf(self):
        connector = RestEnterpriseConnector(
            connector_name="test_internal",
            base_url="http://169.254.169.254",
            enforce_ssrf=True,
        )
        health = await connector.health_check()
        assert health.is_healthy is False
        assert "SSRF violation" in health.details.get("error", "")

        result = await connector.execute_query("get_orders", verified_customer_id="cust_123")
        assert result.success is False
        assert "SSRF violation" in str(result.error_message)

        with pytest.raises(SSRFSecurityViolationError):
            await connector._execute_internal("get_orders", verified_customer_id="cust_123", parameters={}, timeout_seconds=5.0)


@pytest.mark.asyncio
class TestConnectorsApi:
    async def test_connectors_crud_and_ssrf_test_endpoint(self):
        org_id = uuid.uuid4()
        async with async_session_factory() as db_session:
            org = Organization(
                id=org_id,
                slug=f"org-{uuid.uuid4().hex[:8]}",
                name="Connectors Test Org",
                is_active=True,
            )
            db_session.add(org)

            admin_user = User(
                id=uuid.uuid4(),
                organization_id=org_id,
                email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
                display_name="Admin User",
                password_hash=hash_password("password123"),
                role=UserRole.admin,
                is_active=True,
            )
            db_session.add(admin_user)
            await db_session.commit()

        token = create_access_jwt(subject=str(admin_user.id), role="admin", org_id=str(org_id))
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Test SSRF detection on test-connection endpoint
            res = await client.post(
                "/api/v1/connectors/test-connection",
                headers=headers,
                json={"base_url": "http://127.0.0.1:8000"},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is False
            assert data["error_type"] == "SSRF_VIOLATION"

            # 2. Test SSRF rejection when creating connector with private IP
            res_bad_create = await client.post(
                "/api/v1/connectors",
                headers=headers,
                json={
                    "name": "bad_private_connector",
                    "connector_type": "rest",
                    "base_url": "http://192.168.1.50:8000",
                    "auth_type": "bearer",
                    "secret_reference": "SECRET_KEY",
                },
            )
            assert res_bad_create.status_code == 400
            assert "SSRF violation" in res_bad_create.text

            # 3. Create valid connector pointing to public host
            res_create = await client.post(
                "/api/v1/connectors",
                headers=headers,
                json={
                    "name": "valid_public_connector",
                    "connector_type": "rest",
                    "base_url": "https://8.8.8.8",
                    "auth_type": "bearer",
                    "secret_reference": "SECRET_KEY",
                    "config_json": {
                        "operations": {
                            "get_status": {
                                "path": "/status",
                                "method": "GET",
                                "allowed_fields": ["status"],
                            }
                        }
                    },
                },
            )
            assert res_create.status_code == 201
            created = res_create.json()
            connector_id = created["id"]
            assert created["name"] == "valid_public_connector"

            # 4. List connectors
            res_list = await client.get("/api/v1/connectors", headers=headers)
            assert res_list.status_code == 200
            items = res_list.json()
            assert any(c["id"] == connector_id for c in items)

            # 5. Update connector
            res_update = await client.put(
                f"/api/v1/connectors/{connector_id}",
                headers=headers,
                json={"is_enabled": False},
            )
            assert res_update.status_code == 200
            assert res_update.json()["is_enabled"] is False

            # 6. Delete connector
            res_delete = await client.delete(
                f"/api/v1/connectors/{connector_id}",
                headers=headers,
            )
            assert res_delete.status_code == 200
            assert res_delete.json()["status"] == "deleted"
