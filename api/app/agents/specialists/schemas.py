"""Typed, evidence-first contracts returned by Phase 6 specialist agents."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.evidence import VerifiedFact


class SpecialistStatus(StrEnum):
    completed = "completed"
    authentication_required = "authentication_required"
    no_data = "no_data"
    ineligible = "ineligible"
    failed = "failed"


class SpecialistFindings(BaseModel):
    """A bounded, tool-independent record consumed by later orchestration phases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    specialist: str = Field(min_length=1, max_length=64)
    status: SpecialistStatus
    facts: tuple[VerifiedFact, ...] = ()
    evidence_keys: tuple[str, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    recommendations: tuple[str, ...] = ()
    action_draft: dict[str, Any] | None = None
    failure_reason: str | None = Field(default=None, max_length=160)
