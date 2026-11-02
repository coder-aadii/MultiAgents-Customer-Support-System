"""Tests for 5B.3: Parameterized SQL Replica Connector."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.connectors.base import ConnectorValidationError
from app.connectors.sql.connector import SqlEnterpriseConnector
from app.database import engine

APPROVED_TEST_QUERIES = {
    "get_orders": (
        "SELECT id AS order_id, status, total_amount, carrier, tracking_number, created_at AS order_date "
        "FROM test_replica_orders WHERE customer_id = :verified_customer_id ORDER BY created_at DESC LIMIT 10"
    ),
    "get_order_details": (
        "SELECT id AS order_id, status, total_amount, carrier, tracking_number, created_at AS order_date "
        "FROM test_replica_orders WHERE customer_id = :verified_customer_id AND (id = :order_id OR order_number = :order_id) LIMIT 1"
    ),
}


@pytest.fixture
async def test_replica_db():
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS test_replica_orders (
                    id VARCHAR(64) PRIMARY KEY,
                    order_number VARCHAR(64),
                    customer_id VARCHAR(64),
                    status VARCHAR(32),
                    total_amount NUMERIC(10, 2),
                    carrier VARCHAR(64),
                    tracking_number VARCHAR(64),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
                DELETE FROM test_replica_orders;
                INSERT INTO test_replica_orders (id, order_number, customer_id, status, total_amount, carrier, tracking_number)
                VALUES 
                ('ord-1', 'ORD-1001', 'cust_alice', 'delivered', 89.95, 'FedEx', 'TRK-100'),
                ('ord-2', 'ORD-1002', 'cust_alice', 'in_transit', 45.00, 'UPS', 'TRK-200'),
                ('ord-3', 'ORD-2001', 'cust_bob', 'delivered', 120.00, 'DHL', 'TRK-300');
                """
            )
        )
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS test_replica_orders;"))


@pytest.mark.asyncio
async def test_sql_connector_executes_parameterized_query(test_replica_db):
    connector = SqlEnterpriseConnector(
        connector_name="test_sql",
        approved_queries=APPROVED_TEST_QUERIES,
        custom_engine=test_replica_db,
    )

    res = await connector.execute_query(
        operation="get_orders",
        verified_customer_id="cust_alice",
    )
    assert res.success is True
    assert isinstance(res.data, list)
    assert len(res.data) == 2
    assert res.data[0]["order_id"] in ("ord-1", "ord-2")


@pytest.mark.asyncio
async def test_sql_connector_blocks_unauthorized_customer_access(test_replica_db):
    connector = SqlEnterpriseConnector(
        connector_name="test_sql",
        approved_queries=APPROVED_TEST_QUERIES,
        custom_engine=test_replica_db,
    )

    # Bob attempts to get Alice's order ord-1 -> must return NOT_FOUND (BOLA defense)
    res = await connector.execute_query(
        operation="get_order_details",
        verified_customer_id="cust_bob",
        parameters={"order_id": "ord-1"},
    )
    assert res.success is False
    assert res.error_code == "NOT_FOUND"


def test_sql_connector_rejects_ddl_and_dml_statements():
    with pytest.raises(ConnectorValidationError):
        SqlEnterpriseConnector(
            connector_name="unsafe_sql",
            approved_queries={
                "bad_op": "DROP TABLE orders; SELECT * FROM orders WHERE customer_id = :verified_customer_id"
            },
        )


def test_sql_connector_rejects_missing_customer_predicate():
    with pytest.raises(ConnectorValidationError):
        SqlEnterpriseConnector(
            connector_name="missing_pred_sql",
            approved_queries={"unscoped_op": "SELECT * FROM orders WHERE id = :order_id"},
        )
