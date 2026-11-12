from __future__ import annotations

import re

from app.schemas.evidence import VerifiedFact


class PolicyValidatorError(Exception):
    """Raised when policy validation fails on generated output."""


class PolicyValidator:
    """Deterministic rule-based policy gate verifying facts asserted in responses.

    Enforces:
    1. Entity ID Grounding: Any entity ID (e.g. ORD-1001, TRK-8801, REF-5001) mentioned in the
       response text MUST correspond to a registered VerifiedFact.
    2. Monetary Amount Grounding: Any dollar or numeric currency amount mentioned MUST match a
       registered VerifiedFact value. Hallucinated refunds or charges are strictly blocked.
    3. Date Grounding: Any date (YYYY-MM-DD) mentioned MUST match a verified fact.
    4. Safe Failure: Any violation immediately returns (False, reason) so caller can halt delivery
       and route to safe staff handoff.
    """

    ENTITY_ID_REGEX = re.compile(r"\b([A-Z]{3,4}-\d{3,6})\b", re.IGNORECASE)
    MONETARY_REGEX = re.compile(r"(?:\$|\bUSD\s*)\s*(\d[\d,]*(?:\.\d{2})?)\b|\b(\d[\d,]*\.\d{2})\s*(?:USD|dollars)?\b")
    DATE_REGEX = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

    @classmethod
    def validate_response_grounding(
        cls,
        response_text: str,
        verified_facts: list[VerifiedFact],
    ) -> tuple[bool, str | None]:
        """Validates that all stated facts in response_text are backed by verified_facts.

        Returns (True, None) if completely grounded, or (False, reason) on discrepancy.
        """
        if not response_text:
            return False, "Response text is empty"

        # 1. Verify Entity IDs
        mentioned_entity_ids = cls.ENTITY_ID_REGEX.findall(response_text)
        fact_entity_ids = {f.entity_id.upper() for f in verified_facts}
        for f in verified_facts:
            if isinstance(f.field_value, str):
                for val_id in cls.ENTITY_ID_REGEX.findall(f.field_value):
                    fact_entity_ids.add(val_id.upper())

        for ent_id in mentioned_entity_ids:
            if ent_id.upper() not in fact_entity_ids:
                return False, f"Ungrounded entity ID mentioned: '{ent_id}'"

        # 2. Verify Monetary Amounts
        raw_amount_matches = cls.MONETARY_REGEX.findall(response_text)
        mentioned_amounts: list[float] = []
        for match_group in raw_amount_matches:
            val_str = match_group[0] or match_group[1]
            if val_str:
                try:
                    mentioned_amounts.append(float(val_str.replace(",", "")))
                except ValueError:
                    pass

        # Collect verified numeric amounts
        verified_amounts: list[float] = []
        for f in verified_facts:
            if isinstance(f.field_value, int | float):
                verified_amounts.append(float(f.field_value))
            elif isinstance(f.field_value, str):
                try:
                    verified_amounts.append(float(f.field_value))
                except ValueError:
                    pass

        for amount in mentioned_amounts:
            # Check if this amount is within 0.01 of any verified amount
            matched = any(abs(amount - v) < 0.01 for v in verified_amounts)
            if not matched:
                return False, f"Ungrounded monetary amount: ${amount:.2f}"

        # 3. Verify Dates
        mentioned_dates = cls.DATE_REGEX.findall(response_text)
        verified_date_strings = {
            str(f.field_value).strip() for f in verified_facts
        }

        for date_str in mentioned_dates:
            matched_date = any(date_str in v_str for v_str in verified_date_strings)
            if not matched_date:
                return False, f"Ungrounded date mentioned: '{date_str}'"

        return True, None


def validate_response_grounding(
    response_text: str, verified_facts: list[VerifiedFact]
) -> bool:
    """Convenience functional wrapper returning boolean grounding status."""
    is_valid, _ = PolicyValidator.validate_response_grounding(response_text, verified_facts)
    return is_valid
