from __future__ import annotations

import hashlib
import hmac
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import BadRequestError, ConflictError, NotFoundError, UnauthorizedError
from app.config import settings
from app.models.conversation import Conversation
from app.models.csat_survey import CSATSurvey

CSAT_SECRET = settings.JWT_SECRET


class CSATService:
    """Manages secure one-time cryptographic CSAT survey invitations and submissions."""

    @classmethod
    def generate_survey_token(
        cls,
        conversation_id: uuid.UUID,
        customer_id: str | None = None,
    ) -> str:
        """Generates a tamper-proof survey signature token."""
        cid = customer_id or "anonymous"
        ts = int(time.time())
        raw = f"{conversation_id}:{cid}:{ts}"
        sig = hmac.new(CSAT_SECRET.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        return f"{conversation_id}:{cid}:{ts}:{sig}"

    @classmethod
    def verify_survey_token(cls, token: str) -> tuple[uuid.UUID, str]:
        """Validates survey token integrity and returns (conversation_id, customer_id)."""
        parts = token.split(":")
        if len(parts) != 4:
            raise UnauthorizedError("Malformed survey token format")

        conv_id_str, cid, ts_str, provided_sig = parts
        raw = f"{conv_id_str}:{cid}:{ts_str}"
        expected_sig = hmac.new(CSAT_SECRET.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(provided_sig, expected_sig):
            raise UnauthorizedError("Invalid or tampered survey signature")

        try:
            conv_id = uuid.UUID(conv_id_str)
        except ValueError:
            raise UnauthorizedError("Invalid conversation ID in token")

        return conv_id, cid

    @classmethod
    async def submit_survey(
        cls,
        db: AsyncSession,
        token: str,
        rating: int,
        comment: str | None = None,
    ) -> CSATSurvey:
        """Submits verified CSAT rating."""
        if not (1 <= rating <= 5):
            raise BadRequestError("CSAT rating must be between 1 and 5")

        conv_id, cid = cls.verify_survey_token(token)

        # Ensure conversation exists
        conv_stmt = select(Conversation).where(Conversation.id == conv_id)
        conv_res = await db.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()
        if not conv:
            raise NotFoundError("Conversation not found")

        # Check existing survey
        existing_stmt = select(CSATSurvey).where(CSATSurvey.conversation_id == conv_id)
        existing_res = await db.execute(existing_stmt)
        if existing_res.scalar_one_or_none():
            raise ConflictError("A survey response has already been submitted for this conversation")

        survey = CSATSurvey(
            organization_id=conv.organization_id,
            conversation_id=conv_id,
            customer_id=cid if cid != "anonymous" else None,
            rating=rating,
            comment=comment.strip() if comment else None,
        )
        db.add(survey)
        await db.commit()
        await db.refresh(survey)
        return survey
