from __future__ import annotations

from typing import Any

SENSITIVE_KEY_PATTERNS = {
    "password",
    "token",
    "secret",
    "key",
    "authorization",
    "cookie",
    "access_token",
    "refresh_token",
    "jwt",
    "raw_message",
    "raw_document",
    "content_raw",
}

REDACTED_PLACEHOLDER = "[REDACTED]"


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(pattern in lowered for pattern in SENSITIVE_KEY_PATTERNS)


def redact_sensitive_data(val: Any) -> Any:
    """Recursively redact dictionary or list values whose keys are in the sensitive denylist."""
    if isinstance(val, dict):
        redacted = {}
        for k, v in val.items():
            if is_sensitive_key(str(k)):
                redacted[k] = REDACTED_PLACEHOLDER
            else:
                redacted[k] = redact_sensitive_data(v)
        return redacted
    elif isinstance(val, list):
        return [redact_sensitive_data(item) for item in val]
    elif isinstance(val, tuple):
        return tuple(redact_sensitive_data(item) for item in val)
    return val
