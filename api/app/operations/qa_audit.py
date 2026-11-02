from __future__ import annotations

import re
from typing import Any

from app.models.message import Message, SenderType


class QAAuditor:
    """Automated AI compliance and conversation quality scoring engine."""

    @classmethod
    def audit_conversation(cls, messages: list[Message]) -> dict[str, Any]:
        """Evaluates customer-staff transcript against quality rubrics (politeness, groundedness, empathy, accuracy)."""
        assistant_and_staff_messages = [
            m.content for m in messages if m.sender_type in (SenderType.assistant, SenderType.staff)
        ]

        if not assistant_and_staff_messages:
            return {
                "overall_score": 0.0,
                "passed": False,
                "rubrics": {},
                "feedback": "No agent or assistant messages found in conversation.",
            }

        combined_text = " ".join(assistant_and_staff_messages).lower()

        # 1. Politeness Rubric (Max 25 pts)
        politeness_tokens = ["please", "thank you", "thanks", "happy to help", "have a great day", "hello", "hi"]
        polite_hits = sum(1 for t in politeness_tokens if t in combined_text)
        politeness_score = min(25.0, polite_hits * 6.25)

        # 2. Empathy Rubric (Max 25 pts)
        empathy_tokens = ["sorry", "apologize", "understand", "frustrating", "assist you", "resolve this"]
        empathy_hits = sum(1 for t in empathy_tokens if t in combined_text)
        empathy_score = min(25.0, empathy_hits * 8.5)

        # 3. Accuracy & Groundedness Rubric (Max 30 pts)
        # Check for concrete identifiers, reference numbers, or policy citations
        has_identifiers = bool(re.search(r"\b(ord-|trk-|ref-|#|[0-9]{4,})\b", combined_text, re.IGNORECASE))
        has_clear_facts = len(combined_text.split()) > 15
        groundedness_score = 30.0 if (has_identifiers and has_clear_facts) else 20.0

        # 4. Professionalism & Closure (Max 20 pts)
        has_profanity = any(bad in combined_text for bad in ["idiot", "stupid", "shut up", "hate"])
        professionalism_score = 0.0 if has_profanity else 20.0

        overall_score = round(politeness_score + empathy_score + groundedness_score + professionalism_score, 1)
        passed = overall_score >= 75.0

        return {
            "overall_score": overall_score,
            "passed": passed,
            "rubrics": {
                "politeness": politeness_score,
                "empathy": empathy_score,
                "groundedness": groundedness_score,
                "professionalism": professionalism_score,
            },
            "feedback": "Exceeds compliance standards" if passed else "Needs coaching on empathy or structured resolution",
        }

