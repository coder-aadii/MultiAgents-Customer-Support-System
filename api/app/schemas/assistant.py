from __future__ import annotations

import enum
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


from app.schemas.message import HTML_TAG_REGEX


class TriageAction(str, enum.Enum):
    answer = "answer"
    business_data = "business_data"
    clarify = "clarify"
    handoff = "handoff"


class TriageReason(str, enum.Enum):
    human_request = "human_request"
    account_or_billing_request = "account_or_billing_request"
    business_data_request = "business_data_request"
    authentication_required = "authentication_required"
    unsafe_domain = "unsafe_domain"
    missing_detail = "missing_detail"
    faq_match = "faq_match"
    irrelevant = "irrelevant"
    unclear = "unclear"
    unknown = "unknown"
    schema_error = "schema_error"


class TriageResult(BaseModel):
    """Validated output from triage classification."""

    model_config = ConfigDict(frozen=True)

    action: TriageAction
    reason: str = Field(default="unknown", max_length=120)

    @classmethod
    def fallback_handoff(cls, reason: str = "schema_error") -> TriageResult:
        return cls(action=TriageAction.handoff, reason=reason)


class GeneratorAction(str, enum.Enum):
    answer = "answer"
    handoff = "handoff"


class GeneratorResult(BaseModel):
    """Validated structured output from answer generator."""

    model_config = ConfigDict(frozen=True)

    action: GeneratorAction
    answer: str | None = Field(default=None, min_length=1, max_length=800)
    cited_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str | None = Field(default=None, max_length=120)

    @field_validator("answer")
    @classmethod
    def validate_no_html(cls, v: str | None) -> str | None:
        if v is None:
            return None

        # Reject HTML markup while preserving mathematical and logical operators (e.g. 5 < 10 and 20 > 15)
        if HTML_TAG_REGEX.search(v):
            raise ValueError("HTML tags are forbidden in generated answers")
        return v

    @classmethod
    def fallback_handoff(cls, reason: str = "unsupported_or_failed") -> GeneratorResult:
        return cls(
            action=GeneratorAction.handoff,
            answer=None,
            cited_chunk_ids=[],
            confidence=0.0,
            reason=reason,
        )

