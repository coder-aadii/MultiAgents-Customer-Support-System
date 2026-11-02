"""Tests for Olist real e-commerce data connector & store auth routes."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.gateway.base import EntityNotFoundError
from app.gateway.connectors.olist_connector import OlistECommerceConnector
from app.main import app
from app.security.customer_auth import CustomerTokenVerifier


@pytest.fixture
def olist_connector():
    return OlistECommerceConnector()


@pytest.mark.asyncio
async def test_olist_connector_capabilities(olist_connector: OlistECommerceConnector):
    capabilities = olist_connector.get_capabilities()
    names = [c.name for c in capabilities]
    assert "get_order_summary" in names
    assert "list_customer_orders" in names
    assert "get_shipment_status" in names
    assert "get_refund_status" in names


@pytest.mark.asyncio
async def test_olist_connector_delivered_order(olist_connector: OlistECommerceConnector):
    # Customer 3ce436f183e68e07877b285a838db11a owns order 00010242fe8c5a6d1ba2dd792cb16214
    customer_id = "3ce436f183e68e07877b285a838db11a"
    order_id = "00010242fe8c5a6d1ba2dd792cb16214"

    record = await olist_connector.get_entity_summary("order", order_id, customer_id)
    assert record.entity_id == order_id
    assert record.customer_id == customer_id
    assert record.summary_fields["status"] == "delivered"
    assert record.summary_fields["carrier"] == "Correios Express"
    assert record.summary_fields["total_amount"] == 72.19
    assert record.summary_fields["tracking_number"] == "BR-00010242"


@pytest.mark.asyncio
async def test_olist_connector_shipped_order(olist_connector: OlistECommerceConnector):
    # Customer caded193e8e47b8362864762a83db3c5 owns order ee64d42b8cf066f35eac1cf57de1aa85
    customer_id = "caded193e8e47b8362864762a83db3c5"
    order_id = "ee64d42b8cf066f35eac1cf57de1aa85"

    record = await olist_connector.get_entity_summary("order", order_id, customer_id)
    assert record.entity_id == order_id
    assert record.summary_fields["status"] == "shipped"
    assert record.summary_fields["carrier"] == "Correios Express"


@pytest.mark.asyncio
async def test_olist_connector_bola_isolation_rejected(olist_connector: OlistECommerceConnector):
    # Customer A attempts to access Customer B's order -> must raise EntityNotFoundError
    customer_a = "3ce436f183e68e07877b285a838db11a"
    order_b = "ee64d42b8cf066f35eac1cf57de1aa85"  # belongs to customer caded193...

    with pytest.raises(EntityNotFoundError):
        await olist_connector.get_entity_summary("order", order_b, customer_a)


@pytest.mark.asyncio
async def test_olist_connector_multi_orders(olist_connector: OlistECommerceConnector):
    # Customer 1bd3585471932167ab72a84955ebefea has 10+ orders in history
    customer_id = "1bd3585471932167ab72a84955ebefea"
    orders = await olist_connector.list_customer_entities("order", customer_id)
    assert len(orders) >= 2
    for o in orders:
        assert o.entity_type == "order"
        assert o.summary_fields.get("status") is not None


@pytest.mark.asyncio
async def test_olist_connector_mock_backward_compatibility(olist_connector: OlistECommerceConnector):
    # Verified mock customer and ORD-1001 must continue to work perfectly
    record = await olist_connector.get_entity_summary("order", "ORD-1001", "cust_verified_123")
    assert record.entity_id == "ORD-1001"
    assert record.summary_fields["status"] == "delivered"
    assert record.summary_fields["tracking_number"] == "TRK-8801"

    # Mock shipment
    trk = await olist_connector.get_entity_summary("shipment", "TRK-8801", "cust_verified_123")
    assert trk.entity_id == "TRK-8801"

    # Mock refund
    ref = await olist_connector.get_entity_summary("refund", "REF-5001", "cust_verified_123")
    assert ref.entity_id == "REF-5001"


@pytest.mark.asyncio
async def test_store_auth_login_real_customer():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Login with email and password Asdf@1234
        response = await client.post(
            "/api/v1/store/auth/login",
            json={
                "identifier": "cust_3ce436f1@olist-store.com",
                "password": "Asdf@1234",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["customer_id"] == "3ce436f183e68e07877b285a838db11a"
        assert data["email"] == "cust_3ce436f1@olist-store.com"
        assert data["token"] is not None

        # Verify minted token cryptographically
        verifier = CustomerTokenVerifier()
        identity = verifier.verify_token(data["token"])
        assert identity.customer_id == "3ce436f183e68e07877b285a838db11a"
        assert identity.identity_assurance_level == 2


@pytest.mark.asyncio
async def test_store_auth_login_wrong_password():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/store/auth/login",
            json={
                "identifier": "cust_3ce436f1@olist-store.com",
                "password": "WrongPassword!999",
            },
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_store_auth_sample_customers():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/store/auth/sample-customers")
        assert response.status_code == 200
        profiles = response.json()
        assert len(profiles) >= 4
        assert any(p["customer_id"] == "3ce436f183e68e07877b285a838db11a" for p in profiles)
        assert any(p["customer_id"] == "cust_verified_123" for p in profiles)

