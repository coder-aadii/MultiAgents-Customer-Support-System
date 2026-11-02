from __future__ import annotations

CLASSIFIER_SYSTEM_PROMPT = """You classify one customer-support message. Return JSON only.
Actions: answer, business_data, clarify, handoff.
Choose business_data for customer-specific order lookups, shipment tracking, refund status, or account data inquiries.
Choose answer for questions answerable from public documentation, FAQ, or general return policies.
Choose handoff for explicit human requests, unsafe domains, cancellations, or complaints.
Choose clarify only for missing details needed to identify an inquiry.
Schema: {"action":"answer|business_data|clarify|handoff","reason":"short_machine_reason"}.
"""

