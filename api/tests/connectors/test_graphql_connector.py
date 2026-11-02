"""Tests for 5B.4: GraphQL Connector & Query Guardrails."""

from __future__ import annotations

import httpx
import pytest

from app.connectors.base import ConnectorValidationError
from app.connectors.graphql.connector import GraphQLEnterpriseConnector


@pytest.mark.asyncio
async def test_graphql_connector_binds_customer_variable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONNECTOR_TEST_GQL_SECRET", "gql-secret-token")

    sent_payloads: list[dict] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content.decode("utf-8"))
        sent_payloads.append(body)
        return httpx.Response(
            200,
            json={
                "data": {
                    "customer": {
                        "orders": [
                            {"id": "GQL-ORD-1", "status": "shipped", "totalAmount": 150.0}
                        ]
                    }
                }
            },
        )

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)

    connector = GraphQLEnterpriseConnector(
        connector_name="test_gql",
        endpoint_url="https://api.store.example.com/graphql",
        secret_reference="test_gql",
        custom_client=client,
    )

    res = await connector.execute_query(
        operation="get_orders",
        verified_customer_id="cust_verified_777",
    )

    assert res.success is True
    assert len(sent_payloads) == 1
    assert sent_payloads[0]["variables"]["customerId"] == "cust_verified_777"
    assert res.data["customer"]["orders"][0]["id"] == "GQL-ORD-1"


def test_graphql_connector_rejects_deeply_nested_queries():
    deep_query = """
    query DeepExploit($customerId: ID!) {
        customer(id: $customerId) {
            orders {
                items {
                    product {
                        seller {
                            address {
                                country {
                                    name
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    """
    with pytest.raises(ConnectorValidationError):
        GraphQLEnterpriseConnector(
            connector_name="deep_gql",
            approved_operations={"deep_op": deep_query},
            max_depth=3,
        )


def test_graphql_connector_rejects_introspection_queries():
    introspection_query = """
    query Introspect($customerId: ID!) {
        __schema {
            types {
                name
            }
        }
    }
    """
    with pytest.raises(ConnectorValidationError):
        GraphQLEnterpriseConnector(
            connector_name="intro_gql",
            approved_operations={"intro_op": introspection_query},
        )

