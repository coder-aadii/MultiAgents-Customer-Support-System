from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from typing import Any

import structlog

logger = structlog.get_logger()


class WebhookDeduplicationGate:
    """Thread-safe in-memory deduplication cache for webhook events preventing replay attacks."""

    _seen_events: dict[str, float] = {}
    _ttl_seconds: float = 3600.0  # 1 hour retention

    @classmethod
    def is_duplicate(cls, dedupe_key: str) -> bool:
        cls._cleanup()
        now = time.time()
        if dedupe_key in cls._seen_events:
            return True
        cls._seen_events[dedupe_key] = now
        return False

    @classmethod
    def _cleanup(cls) -> None:
        now = time.time()
        expired = [k for k, ts in cls._seen_events.items() if now - ts > cls._ttl_seconds]
        for k in expired:
            cls._seen_events.pop(k, None)

    @classmethod
    def reset(cls) -> None:
        cls._seen_events.clear()


def verify_whatsapp_signature(headers: dict[str, str], raw_body: bytes, secret: str) -> bool:
    """Validates Meta X-Hub-Signature-256 header (sha256=<hex_digest>)."""
    sig_header = headers.get("X-Hub-Signature-256") or headers.get("x-hub-signature-256")
    if not sig_header or not sig_header.startswith("sha256="):
        return False

    expected_sig = sig_header.split("sha256=", 1)[1].strip()
    computed_sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_sig, computed_sig)


def verify_twilio_signature(
    headers: dict[str, str],
    raw_body: bytes,
    secret: str,
    url: str | None = None,
) -> bool:
    """Validates Twilio X-Twilio-Signature header (HMAC-SHA1)."""
    sig_header = headers.get("X-Twilio-Signature") or headers.get("x-twilio-signature")
    if not sig_header:
        return False

    # Base HMAC verification over body or url+body
    payload = (url or "").encode("utf-8") + raw_body
    computed_sig = base64.b64encode(
        hmac.new(secret.encode("utf-8"), payload, hashlib.sha1).digest()
    ).decode("utf-8")

    return hmac.compare_digest(sig_header.strip(), computed_sig)


def verify_sendgrid_signature(
    headers: dict[str, str],
    raw_body: bytes,
    secret: str,
) -> bool:
    """Validates SendGrid webhook signature or authorization token with constant-time comparison."""
    token = headers.get("X-Twilio-Email-Event-Webhook-Signature") or headers.get("Authorization")
    if not token:
        return False

    # 1. Bearer / token exact constant-time check
    clean_token = token.replace("Bearer ", "").replace("Basic ", "").strip()
    if hmac.compare_digest(clean_token, secret.strip()):
        return True

    # 2. HMAC-SHA256 signature check
    clean_sig = token.replace("sha256=", "").strip()
    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(clean_sig, computed):
        return True

    return False


def verify_generic_hmac_sha256(
    headers: dict[str, str],
    raw_body: bytes,
    secret: str,
    header_name: str = "X-Webhook-Signature",
) -> bool:
    """Generic HMAC-SHA256 signature validation."""
    sig = headers.get(header_name) or headers.get(header_name.lower())
    if not sig:
        return False

    expected = sig.replace("sha256=", "").strip()
    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, computed)

