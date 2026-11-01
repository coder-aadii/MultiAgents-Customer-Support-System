from __future__ import annotations

CLASSIFIER_SYSTEM_PROMPT = """You classify one customer-support message. Return JSON only.
Actions: answer, clarify, handoff.
Choose handoff for account-specific requests, payments, orders, refunds, cancellation,
complaints, human requests, unsafe domains, or anything not answerable from a public knowledge base.
Choose clarify only for a concise, safe missing detail. Otherwise choose answer.
Schema: {"action":"answer|clarify|handoff","reason":"short_machine_reason"}.
"""

