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
    "database_url",
    "db_password",
    "connection_url",
    "prompt",
    "completion",
    "raw_completion",
    "messages",
    "visitor_message",
    "system_prompt",
    "user_prompt",
    "assistant_message",
    "api_key",
}

REDACTED_PLACEHOLDER = "[REDACTED]"


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(pattern in lowered for pattern in SENSITIVE_KEY_PATTERNS)


def redact_sensitive_data(val: Any) -> Any:
    """Recursively redact dictionary or list values whose keys match sensitive patterns."""
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


def redact_llm_payload_for_logging(payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitize LLM request/response payload for logging.

    Preserves operational telemetry (model, token usage, message count, parameters)
    while strictly removing prompts, completions, and message contents.
    """
    safe: dict[str, Any] = {}
    for k, v in payload.items():
        if k == "messages" and isinstance(v, list):
            safe["message_count"] = len(v)
        elif k in ("prompt", "completion", "raw_completion", "content"):
            safe[k] = REDACTED_PLACEHOLDER
        elif is_sensitive_key(k):
            safe[k] = REDACTED_PLACEHOLDER
        elif isinstance(v, dict):
            safe[k] = redact_llm_payload_for_logging(v)
        else:
            safe[k] = v
    return safe
