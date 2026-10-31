from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from fastapi import Request

from app.common.errors import RateLimitedError
from app.config import settings
from app.models.visitor_session import VisitorSession


class SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding window rate limiter."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._history: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, max_requests: int, window_seconds: int) -> None:
        if not getattr(settings, "MCSS_RATE_LIMIT_ENABLED", True):
            return

        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            timestamps = self._history[key]
            # Prune timestamps older than cutoff
            self._history[key] = [t for t in timestamps if t > cutoff]

            if len(self._history[key]) >= max_requests:
                raise RateLimitedError(
                    f"Rate limit exceeded: maximum {max_requests} requests per {window_seconds}s"
                )

            self._history[key].append(now)

    def reset(self) -> None:
        with self._lock:
            self._history.clear()


limiter = SlidingWindowRateLimiter()


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def rate_limit_login(request: Request, email: str) -> None:
    ip = get_client_ip(request)
    key = f"login:{ip}:{email.strip().lower()}"
    # 5 attempts / 15 minutes (900 seconds)
    limiter.check(key=key, max_requests=5, window_seconds=900)


def rate_limit_widget_session(request: Request, origin: str | None) -> None:
    ip = get_client_ip(request)
    origin_key = origin.strip().lower() if origin else "no_origin"
    key = f"session:{ip}:{origin_key}"
    # 30 requests / 1 hour (3600 seconds)
    limiter.check(key=key, max_requests=30, window_seconds=3600)


def rate_limit_visitor_message(session: VisitorSession) -> None:
    key = f"visitor_msg:{session.visitor_id}"
    # 10 messages / 1 minute (60 seconds)
    limiter.check(key=key, max_requests=10, window_seconds=60)

