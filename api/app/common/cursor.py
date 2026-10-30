from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

from fastapi import status

from app.common.errors import AppException
from app.schemas.common import ErrorCode


def encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    """Encode a timestamp and UUID into an opaque URL-safe string."""
    # Ensure UTC timezone
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)

    raw = f"{created_at.isoformat()}|{item_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Decode an opaque cursor into (created_at, item_id).

    Raises AppException(VALIDATION_ERROR, status_code=400) if malformed.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        parts = raw.split("|", 1)
        if len(parts) != 2:
            raise ValueError("Malformed cursor format")

        ts_str, id_str = parts
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)

        item_id = uuid.UUID(id_str)
        return dt, item_id
    except Exception as e:
        raise AppException(
            code=ErrorCode.VALIDATION_ERROR,
            message="Invalid pagination cursor",
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"cursor": "Cursor must be a valid base64-encoded timestamp and UUID tuple"},
        ) from e
