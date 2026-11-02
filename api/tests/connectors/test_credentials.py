"""Tests for 5B.1: Credential security, environment variable resolution, and masking."""

from __future__ import annotations

import os
import pytest

from app.connectors.base import ConnectorAuthError
from app.connectors.credentials import CredentialManager


def test_credential_manager_loads_and_masks_secrets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONNECTOR_SHOPIFY_SECRET", "shpat_abc123456789xyz")

    secret = CredentialManager.resolve_secret("shopify")
    assert secret == "shpat_abc123456789xyz"

    masked = CredentialManager.mask_secret(secret)
    assert masked.startswith("sh***yz")
    assert "abc123456789" not in masked


def test_credential_manager_raises_on_missing_secret():
    with pytest.raises(ConnectorAuthError):
        CredentialManager.resolve_secret("non_existent_service_secret_ref_999")


def test_credential_manager_redacts_authorization_headers():
    headers = {
        "Authorization": "Bearer super-secret-token",
        "X-Api-Key": "api-key-12345",
        "Cookie": "session_id=abcdef",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    clean = CredentialManager.sanitize_headers(headers)
    assert clean["Authorization"] == "[REDACTED]"
    assert clean["X-Api-Key"] == "[REDACTED]"
    assert clean["Cookie"] == "[REDACTED]"
    assert clean["Content-Type"] == "application/json"


def test_credential_manager_sanitizes_nested_data():
    payload = {
        "customer_id": "cust_123",
        "user_token": "secret_token_val",
        "account": {
            "password_hash": "hash123",
            "name": "Alice Smith",
        },
        "orders": [
            {"id": "ORD-1", "credit_card_number": "4111222233334444"},
            {"id": "ORD-2", "total": 45.0},
        ],
    }
    sanitized = CredentialManager.sanitize_data(payload)
    assert sanitized["customer_id"] == "cust_123"
    assert sanitized["user_token"] == "[REDACTED]"
    assert sanitized["account"]["password_hash"] == "[REDACTED]"
    assert sanitized["account"]["name"] == "Alice Smith"
    assert sanitized["orders"][0]["credit_card_number"] == "[REDACTED]"
    assert sanitized["orders"][1]["total"] == 45.0

