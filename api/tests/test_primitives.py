"""Tests for Sub-phase 1.3 Shared API primitives."""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.cursor import decode_cursor, encode_cursor
from app.common.errors import (
    AppException,
)
from app.common.redaction import redact_sensitive_data
from app.main import app


@pytest.mark.asyncio
async def test_request_id_propagation():
    """Verify X-Request-ID header is propagated from request to response."""
    transport = ASGITransport(app=app)
    req_id = "test-custom-request-id-12345"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz", headers={"X-Request-ID": req_id})
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID") == req_id


@pytest.mark.asyncio
async def test_generated_request_id_when_missing():
    """Verify server generates a valid UUID request ID when missing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        generated_id = resp.headers.get("X-Request-ID")
        assert generated_id is not None
        uuid.UUID(generated_id)


@pytest.mark.asyncio
async def test_not_found_error_envelope():
    """Verify 404 returns stable error envelope with request ID and code."""
    transport = ASGITransport(app=app)
    req_id = "test-404-id"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/nonexistent-endpoint-path", headers={"X-Request-ID": req_id})
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == "NOT_FOUND"
        assert "request_id" in data["error"]
        assert data["error"]["request_id"] == req_id
        assert resp.headers.get("X-Request-ID") == req_id


@pytest.mark.asyncio
async def test_api_v1_ping():
    """Verify /api/v1 router is mounted and responsive."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/ping")
        assert resp.status_code == 200
        assert resp.json() == {"status": "pong"}


def test_cursor_encode_decode():
    """Verify cursor encoding and decoding preserves timestamp and UUID."""
    now = datetime.now(UTC)
    item_id = uuid.uuid4()

    cursor = encode_cursor(now, item_id)
    assert isinstance(cursor, str)

    decoded_dt, decoded_id = decode_cursor(cursor)
    assert decoded_dt == now
    assert decoded_id == item_id


def test_invalid_cursor_raises_validation_error():
    """Verify malformed cursors raise 400 VALIDATION_ERROR."""
    with pytest.raises(AppException) as exc_info:
        decode_cursor("not-valid-base64!!!")
    assert exc_info.value.code == "VALIDATION_ERROR"
    assert exc_info.value.status_code == 400

    with pytest.raises(AppException) as exc_info:
        # Valid base64 but invalid content
        decode_cursor("dGVzdA==")
    assert exc_info.value.code == "VALIDATION_ERROR"
    assert exc_info.value.status_code == 400


def test_cursor_deterministic_tie_breaking():
    """Verify identical timestamps are deterministically sorted by UUID."""
    same_time = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    id1 = uuid.UUID("00000000-0000-0000-0000-000000000001")
    id2 = uuid.UUID("00000000-0000-0000-0000-000000000002")

    c1 = encode_cursor(same_time, id1)
    c2 = encode_cursor(same_time, id2)

    assert c1 != c2

    dt1, decoded_id1 = decode_cursor(c1)
    dt2, decoded_id2 = decode_cursor(c2)

    items = [(dt2, decoded_id2), (dt1, decoded_id1)]
    # Sorting by (timestamp, id) should place id1 before id2
    sorted_items = sorted(items, key=lambda x: (x[0], x[1]))
    assert sorted_items[0][1] == id1
    assert sorted_items[1][1] == id2


def test_redaction_helper():
    """Verify sensitive fields in denylist are replaced with [REDACTED]."""
    payload = {
        "user": {
            "email": "test@example.com",
            "password": "super-secret-password",
            "access_token": "jwt-token-12345",
            "nested": {
                "secret_key": "private-key-data",
                "normal_field": "visible",
            },
        },
        "authorization": "Bearer secret-token",
        "raw_message": "private raw content",
        "public_title": "Hello World",
    }

    redacted = redact_sensitive_data(payload)

    assert redacted["user"]["email"] == "test@example.com"
    assert redacted["user"]["password"] == "[REDACTED]"
    assert redacted["user"]["access_token"] == "[REDACTED]"
    assert redacted["user"]["nested"]["secret_key"] == "[REDACTED]"
    assert redacted["user"]["nested"]["normal_field"] == "visible"
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["raw_message"] == "[REDACTED]"
    assert redacted["public_title"] == "Hello World"
