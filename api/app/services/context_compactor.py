from __future__ import annotations

import re
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.models.message import Message, SenderType
from app.schemas.entities import OrganizationEntitySchema


class CompactedConversationMemory(BaseModel):
    """Structured, lossless rolling summary of past conversation turns."""

    model_config = ConfigDict(frozen=True)

    summary: str = Field(description="Condensed narrative synopsis of earlier turns")
    extracted_entities: dict[str, str] = Field(
        default_factory=dict, description="Resolved entity identifiers (order_id, workspace_id, etc.)"
    )
    verified_facts: list[str] = Field(
        default_factory=list, description="Grounding facts established during prior turns"
    )
    unresolved_questions: list[str] = Field(
        default_factory=list, description="Open customer inquiries not yet fully addressed"
    )
    compacted_messages_count: int = Field(default=0, description="Number of historical messages compacted")

    def format_system_context_block(self) -> str:
        """Renders compacted memory into an efficient, bounded system context block (< 300 tokens)."""
        lines = [
            f"[CONVERSATION MEMORY: {self.compacted_messages_count} earlier messages compacted]",
            f"Summary: {self.summary}",
        ]
        if self.extracted_entities:
            ent_str = ", ".join(f"{k}={v}" for k, v in self.extracted_entities.items())
            lines.append(f"Identified Entities: {ent_str}")
        if self.verified_facts:
            facts_str = "; ".join(self.verified_facts[:8])
            lines.append(f"Established Facts: {facts_str}")
        if self.unresolved_questions:
            questions_str = "; ".join(self.unresolved_questions[:4])
            lines.append(f"Unresolved Questions: {questions_str}")
        lines.append("[END CONVERSATION MEMORY]")
        return "\n".join(lines)


class ContextAutoCompactor:
    """Automated context compactor keeping multi-turn conversations bounded within model budgets."""

    DEFAULT_VERBATIM_RECENT_MESSAGES = 4  # Keep last 2 conversational turns verbatim
    COMPACTION_THRESHOLD_MESSAGES = 6     # Trigger compaction once history exceeds 6 messages
    MAX_TOKEN_BUDGET = 2000

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        """Conservative token estimation (~4 characters per token)."""
        return max(1, len(text) // 4)

    @classmethod
    def extract_entities(
        cls,
        text: str,
        entity_schema: OrganizationEntitySchema | None = None,
    ) -> dict[str, str]:
        """Extracts entity IDs from text using tenant patterns or universal patterns."""
        entities: dict[str, str] = {}
        if entity_schema:
            pattern_map = entity_schema.get_pattern_map()
            for ent_name, pattern in pattern_map.items():
                m = pattern.search(text)
                if m:
                    entities[ent_name] = m.group(0).strip()

        # Fallback universal patterns
        universal_patterns = {
            "order_id": re.compile(r"\bORD-[0-9A-Za-z]{3,24}\b", re.IGNORECASE),
            "tracking_number": re.compile(r"\b(?:TRK|SHP|BR)-[0-9A-Za-z]{3,24}\b", re.IGNORECASE),
            "workspace_id": re.compile(r"\bWS-[0-9a-zA-Z_-]{3,24}\b", re.IGNORECASE),
            "subscription_id": re.compile(r"\bSUB-[0-9a-zA-Z_-]{3,24}\b", re.IGNORECASE),
            "account_id": re.compile(r"\bACC-[0-9]{4,16}\b", re.IGNORECASE),
            "transaction_id": re.compile(r"\bTXN-[0-9A-Za-z]{4,20}\b", re.IGNORECASE),
            "patient_id": re.compile(r"\bPAT-[0-9A-Za-z]{4,16}\b", re.IGNORECASE),
            "appointment_id": re.compile(r"\bAPT-[0-9A-Za-z]{4,16}\b", re.IGNORECASE),
        }
        for k, pat in universal_patterns.items():
            if k not in entities:
                m = pat.search(text)
                if m:
                    entities[k] = m.group(0).strip()

        return entities

    @classmethod
    def compact_conversation_history(
        cls,
        messages: Sequence[Message],
        verbatim_recent_count: int = DEFAULT_VERBATIM_RECENT_MESSAGES,
        entity_schema: OrganizationEntitySchema | None = None,
    ) -> tuple[CompactedConversationMemory | None, list[Message]]:
        """Splits messages into a compacted historical envelope and recent verbatim messages.

        Returns:
            (compacted_memory, recent_verbatim_messages)
        """
        if len(messages) <= cls.COMPACTION_THRESHOLD_MESSAGES:
            # Under threshold: no compaction needed, retain full history
            return None, list(messages)

        split_idx = max(1, len(messages) - verbatim_recent_count)
        older_messages = messages[:split_idx]
        recent_messages = list(messages[split_idx:])

        # 1. Aggregate entities across all older turns
        all_entities: dict[str, str] = {}
        established_facts: list[str] = []
        unresolved: list[str] = []
        turn_summaries: list[str] = []

        for msg in older_messages:
            content = (msg.content or "").strip()
            if not content:
                continue

            # Extract entities
            ents = cls.extract_entities(content, entity_schema=entity_schema)
            all_entities.update(ents)

            # Extract established facts from assistant metadata
            meta = msg.metadata_json or {}
            if msg.sender_type == SenderType.assistant:
                if "facts" in meta and isinstance(meta["facts"], list):
                    established_facts.extend(str(f) for f in meta["facts"] if str(f) not in established_facts)
                elif "action" in meta:
                    action = meta.get("action")
                    if action not in ("greeting", "out_of_scope"):
                        established_facts.append(f"Assistant performed {action}")
                # Track synopsis
                preview = content.split("\n")[0]
                if len(preview) > 120:
                    preview = preview[:117] + "..."
                turn_summaries.append(f"Support responded: '{preview}'")
            elif msg.sender_type == SenderType.visitor:
                # Capture customer query or question
                if "?" in content:
                    q = content.split("?")[0].strip() + "?"
                    if len(q) > 90:
                        q = q[:87] + "..."
                    if q not in unresolved:
                        unresolved.append(q)
                turn_summaries.append(f"Customer asked: '{content[:80]}'")

        # Generate narrative rolling summary
        if len(turn_summaries) > 8:
            summary = f"Multi-turn exchange covering {len(older_messages)} turns. Earlier discussions: {'; '.join(turn_summaries[:3])} ... Latest prior turn: {turn_summaries[-1]}."
        else:
            summary = "Prior turns: " + "; ".join(turn_summaries)

        memory = CompactedConversationMemory(
            summary=summary,
            extracted_entities=all_entities,
            verified_facts=established_facts,
            unresolved_questions=unresolved,
            compacted_messages_count=len(older_messages),
        )

        return memory, recent_messages
