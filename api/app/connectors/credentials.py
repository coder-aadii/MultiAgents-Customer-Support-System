from __future__ import annotations

import os
import re
from typing import Any

import structlog

from app.connectors.base import ConnectorAuthError

logger = structlog.get_logger()

SENSITIVE_HEADER_KEYS = {
    "authorization",
    "x-api-key",
    "x-auth-token",
    "cookie",
    "proxy-authorization",
    "set-cookie",
}

SENSITIVE_FIELD_PATTERNS = re.compile(
    r"(password|secret|token|api_?key|auth|credit_?card|cvv|ssn)", re.IGNORECASE
)


class CredentialManager:
    """Secure credential resolver and redactor for external enterprise connectors.

    Enforces:
    - Zero raw credentials in logs or serialization.
    - Safe environment variable and reference lookup.
    - Sanitization of outbound HTTP headers and exception messages.
    """

    @classmethod
    def resolve_secret(cls, secret_reference: str) -> str:
        """Resolves secret from environment variable or reference key.

        Raises ConnectorAuthError if secret is unconfigured or empty.
        """
        if not secret_reference or not isinstance(secret_reference, str):
            raise ConnectorAuthError("Secret reference must be a non-empty string")

        clean_ref = secret_reference.strip()
        if clean_ref.startswith("env:"):
            clean_ref = clean_ref[4:]

        # Lookup exact name, uppercase name, or CONNECTOR_<REF>
        candidates = [
            clean_ref,
            clean_ref.upper(),
            f"CONNECTOR_{clean_ref.upper()}",
            f"CONNECTOR_{clean_ref.upper()}_SECRET",
            f"CONNECTOR_{clean_ref.upper()}_API_KEY",
        ]

        for cand in candidates:
            val = os.getenv(cand)
            if val and val.strip():
                return val.strip()

        raise ConnectorAuthError(
            f"Failed to resolve secret for reference '{secret_reference}'. Environment variable not set."
        )

    @classmethod
    def mask_secret(cls, secret: str | None) -> str:
        """Returns a safe, masked representation of a secret for diagnostics."""
        if not secret or not isinstance(secret, str):
            return "[EMPTY]"
        length = len(secret)
        if length <= 4:
            return "***"
        if length <= 8:
            return f"{secret[:1]}***{secret[-1:]}"
        return f"{secret[:2]}***{secret[-2:]} ({length} chars)"

    @classmethod
    def sanitize_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        """Returns headers dict with all sensitive credential headers redacted."""
        clean: dict[str, str] = {}
        for k, v in headers.items():
            if k.lower() in SENSITIVE_HEADER_KEYS:
                clean[k] = "[REDACTED]"
            else:
                clean[k] = v
        return clean

    @classmethod
    def sanitize_data(cls, data: Any) -> Any:
        """Recursively sanitizes sensitive fields in dictionaries or lists."""
        if isinstance(data, dict):
            sanitized: dict[str, Any] = {}
            for k, v in data.items():
                if SENSITIVE_FIELD_PATTERNS.search(str(k)):
                    sanitized[k] = "[REDACTED]"
                else:
                    sanitized[k] = cls.sanitize_data(v)
            return sanitized
        elif isinstance(data, list):
            return [cls.sanitize_data(item) for item in data]
        return data

