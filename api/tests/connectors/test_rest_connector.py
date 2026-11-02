"""Tests for 5B.2: Production Read-Only REST Connector."""

from __future__ import annotations

import httpx
import pytest

from app.connectors.rest.connector import RestEnterpriseConnector


@pytest.mark.asyncio
async def test_rest_connector_injects_verified_customer_id(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONNECTOR_TEST_REST_SECRET", "test-secret-token")

    called_urls: list[str] = []
    received_headers: list[dict[str, str]] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        received_headers.append(dict(request.headers))
        return httpx.Response(
            200,
            json=[
                {
                    "order_id": "ORD-501",
                    "status": "delivered",
                    "total_amount": 99.99,
                    "tracking_number": "TRK-900",
                    "secret_internal_notes": "Do not leak this!",
                }
            ],
        )

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)

    connector = RestEnterpriseConnector(
        connector_name="test_rest",
        base_url="https://api.store.example.com",
        secret_reference="test_rest",
        custom_client=client,
    )

    res = await connector.execute_query(
        operation="get_orders",
        verified_customer_id="cust_alice_88",
    )

    assert res.success is True
    assert called_urls == ["https://api.store.example.com/customers/cust_alice_88/orders"]
    assert received_headers[0]["authorization"] == "Bearer test-secret-token"

    # Verify disallowed fields stripped
    assert isinstance(res.data, list)
    assert res.data[0]["order_id"] == "ORD-501"
    assert res.data[0]["status"] == "delivered"
    assert "secret_internal_notes" not in res.data[0]


@pytest.mark.asyncio
async def test_rest_connector_rejects_missing_customer_id():
    connector = RestEnterpriseConnector(connector_name="test_rest")
    res = await connector.execute_query(
        operation="get_orders",
        verified_customer_id="",
    )
    assert res.success is False
    assert res.error_code == "AUTHORIZATION_REQUIRED"


@pytest.mark.asyncio
async def test_rest_connector_handles_not_found(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONNECTOR_TEST_REST_SECRET", "test-secret")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Customer or order not found"})

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)

    connector = RestEnterpriseConnector(
        connector_name="test_rest",
        secret_reference="test_rest",
        custom_client=client,
    )

    res = await connector.execute_query(
        operation="get_order_details",
        verified_customer_id="cust_123",
        parameters={"order_id": "ORD-999"},
    )
    assert res.success is False
    assert res.error_code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_rest_connector_enforces_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONNECTOR_TEST_REST_SECRET", "test-secret")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Request timed out")

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)

    connector = RestEnterpriseConnector(
        connector_name="test_rest",
        secret_reference="test_rest",
        custom_client=client,
    )

    res = await connector.execute_query(
        operation="get_orders",
        verified_customer_id="cust_123",
        timeout_seconds=1.0,
    )
    assert res.success is False
    assert res.error_code == "TIMEOUT"

