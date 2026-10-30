"""WuKongIM delivery boundary for later conversation events.

Phase 0 defines the client boundary only. The API remains the source of truth and
no message body or credential is sent through this module.
"""

from typing import Any

from app.config import settings


async def publish_event(event_id: str, conversation_id: str, event_type: str) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "conversation_id": conversation_id,
        "type": event_type,
        "endpoint": settings.MCSS_WUKONGIM_API_URL,
    }
