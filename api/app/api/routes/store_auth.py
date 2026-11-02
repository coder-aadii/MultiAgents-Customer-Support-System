from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
from app.gateway.connectors.olist_connector import OlistECommerceConnector
from app.security.customer_auth import create_customer_jwt

store_auth_router = APIRouter(prefix="/store/auth", tags=["Store Auth"])


class StoreLoginRequest(BaseModel):
    identifier: str = Field(description="Customer ID or Email address")
    password: str = Field(description="Customer password (default: Asdf@1234)")


class StoreLoginResponse(BaseModel):
    customer_id: str
    email: str | None = None
    city: str | None = None
    state: str | None = None
    token: str
    assurance_level: int = 2


class SampleCustomerProfile(BaseModel):
    customer_id: str
    email: str
    label: str
    description: str
    password: str = "Asdf@1234"
    sample_queries: list[str] = Field(default_factory=list)


def _get_sqlite_conn() -> sqlite3.Connection:
    connector = OlistECommerceConnector()
    if not connector.db_path.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Store database not found at {connector.db_path}",
        )
    conn = sqlite3.connect(str(connector.db_path))
    conn.row_factory = sqlite3.Row
    return conn


@store_auth_router.post(
    "/login",
    response_model=StoreLoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate external store customer and mint verified customer JWT",
)
async def store_login(payload: StoreLoginRequest) -> StoreLoginResponse:
    identifier = payload.identifier.strip()
    password = payload.password.strip()

    # 1. Handle mock verified customer
    if identifier in ("cust_verified_123", "verified@example.com"):
        if password != "Asdf@1234":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid customer credentials",
            )
        token = create_customer_jwt(
            customer_id="cust_verified_123",
            email="verified@example.com",
            expires_in_minutes=1440,
        )
        return StoreLoginResponse(
            customer_id="cust_verified_123",
            email="verified@example.com",
            city="Springfield",
            state="IL",
            token=token,
            assurance_level=2,
        )

    # 2. Handle mock other customer (for BOLA tests)
    if identifier in ("cust_other_999", "cust_other_456", "other@example.com"):
        if password != "Asdf@1234":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid customer credentials",
            )
        token = create_customer_jwt(
            customer_id="cust_other_999",
            email="other@example.com",
            expires_in_minutes=1440,
        )
        return StoreLoginResponse(
            customer_id="cust_other_999",
            email="other@example.com",
            city="Metropolis",
            state="NY",
            token=token,
            assurance_level=2,
        )

    # 3. Query real SQLite dataset
    conn = _get_sqlite_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT customer_id, customer_unique_id, email, password_hash, city, state
            FROM customers
            WHERE customer_id = ? OR email = ?
            LIMIT 1
            """,
            (identifier, identifier),
        )
        row = cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid customer credentials",
            )

        # Verify password hash (All customers seeded with Asdf@1234)
        entered_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        if entered_hash != row["password_hash"] and password != "Asdf@1234":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid customer credentials",
            )

        cid = row["customer_id"]
        c_email = row["email"]

        token = create_customer_jwt(
            customer_id=cid,
            email=c_email,
            expires_in_minutes=1440,
        )

        return StoreLoginResponse(
            customer_id=cid,
            email=c_email,
            city=row["city"],
            state=row["state"],
            token=token,
            assurance_level=2,
        )
    finally:
        conn.close()


@store_auth_router.get(
    "/sample-customers",
    response_model=list[SampleCustomerProfile],
    summary="List representative store customers for quick UI testing",
)
async def list_sample_customers() -> list[SampleCustomerProfile]:
    return [
        SampleCustomerProfile(
            customer_id="3ce436f183e68e07877b285a838db11a",
            email="cust_3ce436f1@olist-store.com",
            label="Delivered Order Customer (Olist)",
            description="Has delivered order 00010242fe8c5a6d1ba2dd792cb16214 ($72.19, Correios Express)",
            password="Asdf@1234",
            sample_queries=[
                "Where is my order 00010242fe8c5a6d1ba2dd792cb16214?",
                "What are my orders?",
                "Where is shipment BR-00010242?",
            ],
        ),
        SampleCustomerProfile(
            customer_id="caded193e8e47b8362864762a83db3c5",
            email="cust_caded193@olist-store.com",
            label="In-Transit / Shipped Customer (Olist)",
            description="Has order ee64d42b8cf066f35eac1cf57de1aa85 currently in transit",
            password="Asdf@1234",
            sample_queries=[
                "Where is my order ee64d42b8cf066f35eac1cf57de1aa85?",
                "When will my order arrive?",
            ],
        ),
        SampleCustomerProfile(
            customer_id="1bd3585471932167ab72a84955ebefea",
            email="cust_1bd35854@olist-store.com",
            label="Multi-Order Frequent Customer (Olist)",
            description="Frequent customer with 10+ completed orders in history",
            password="Asdf@1234",
            sample_queries=[
                "What are my orders?",
                "List my recent orders",
            ],
        ),
        SampleCustomerProfile(
            customer_id="6d6b50b66d79f80827b6d96751528d30",
            email="cust_6d6b50b6@olist-store.com",
            label="Canceled Order Customer (Olist)",
            description="Has canceled order 1b9ecfe83cdc259250e1a8aca174f0ad",
            password="Asdf@1234",
            sample_queries=[
                "What is the status of order 1b9ecfe83cdc259250e1a8aca174f0ad?",
            ],
        ),
        SampleCustomerProfile(
            customer_id="cust_verified_123",
            email="verified@example.com",
            label="Acme Mock Customer (ORD-1001, REF-5001)",
            description="Mock fixture customer with ORD-1001, ORD-1002, TRK-8801, REF-5001",
            password="Asdf@1234",
            sample_queries=[
                "Where is my order ORD-1001?",
                "Where is shipment TRK-8801?",
                "Check refund REF-5001",
                "What are my orders?",
            ],
        ),
    ]

