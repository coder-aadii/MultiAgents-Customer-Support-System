from __future__ import annotations

from app.gateway.base import EntityRecord


class TemplateEngine:
    """Deterministic, controlled template renderer for standard entity lookups.

    Bypasses LLM generation for unambiguous status queries to eliminate hallucinations,
    reduce latency, and guarantee 100% adherence to verified business data.
    """

    @classmethod
    def render_entity_status(cls, entity: EntityRecord) -> str:
        """Renders an unambiguous, pre-approved deterministic status response."""
        fields = entity.summary_fields
        status = str(fields.get("status", "unknown")).lower().replace("_", " ")

        match entity.entity_type.lower():
            case "order":
                order_id = entity.entity_id
                if "delivered" in status:
                    carrier = fields.get("carrier", "carrier")
                    delivered_at = fields.get("delivered_at", "recently")
                    tracking = fields.get("tracking_number")
                    trk_str = f" (Tracking: {tracking})" if tracking else ""
                    return (
                        f"Order {order_id} was delivered on {delivered_at} via {carrier}{trk_str}."
                    )
                elif "in transit" in status or "shipped" in status:
                    carrier = fields.get("carrier", "carrier")
                    estimated = fields.get("estimated_delivery", "soon")
                    tracking = fields.get("tracking_number")
                    trk_str = f" (Tracking: {tracking})" if tracking else ""
                    return (
                        f"Order {order_id} is currently in transit with {carrier}. "
                        f"The estimated delivery date is {estimated}{trk_str}."
                    )
                else:
                    currency = fields.get("currency", "USD")
                    total = fields.get("total_amount", 0.0)
                    return f"Order {order_id} is currently {status}. Total: {currency} {total:.2f}."

            case "shipment":
                trk = entity.entity_id
                order_id = fields.get("order_id", "your order")
                carrier = fields.get("carrier", "carrier")
                if "delivered" in status:
                    delivered_at = fields.get("delivered_at", "recently")
                    return f"Shipment {trk} for order {order_id} was delivered on {delivered_at} via {carrier}."
                else:
                    estimated = fields.get("estimated_delivery", "soon")
                    return (
                        f"Shipment {trk} for order {order_id} is currently in transit with {carrier}. "
                        f"Estimated delivery is {estimated}."
                    )

            case "refund":
                ref_id = entity.entity_id
                order_id = fields.get("order_id", "your order")
                amount = fields.get("amount", 0.0)
                currency = fields.get("currency", "USD")
                return (
                    f"Refund {ref_id} for order {order_id} in the amount of {currency} {amount:.2f} is currently {status}."
                )

            case _:
                return f"{entity.title} is currently {status}."

    @classmethod
    def render_entity_list(cls, entity_type: str, entities: list[EntityRecord]) -> str:
        """Renders a deterministic summary list of customer entities."""
        if not entities:
            return f"You currently have no {entity_type}s on file."

        lines = [f"You have {len(entities)} recent {entity_type}(s):"]
        for e in entities:
            status = str(e.summary_fields.get("status", "unknown")).replace("_", " ")
            currency = e.summary_fields.get("currency", "USD")
            total = e.summary_fields.get("total_amount")
            if total is not None:
                lines.append(f"- {e.title} ({status}, {currency} {total:.2f})")
            else:
                lines.append(f"- {e.title} ({status})")

        return "\n".join(lines)

    @classmethod
    def render_entity_count(cls, entity_type: str, entities: list[EntityRecord]) -> str:
        """Renders a humane summary of customer entity count and recent items."""
        count = len(entities)
        if count == 0:
            return f"You have not placed any {entity_type}s with us so far."
        if count == 1:
            e = entities[0]
            status = str(e.summary_fields.get("status", "unknown")).replace("_", " ")
            currency = e.summary_fields.get("currency", "USD")
            total = e.summary_fields.get("total_amount")
            total_str = f", {currency} {total:.2f}" if total is not None else ""
            return f"You have placed 1 {entity_type} with us so far:\n- {e.title} ({status}{total_str})."

        lines = [f"You have placed {count} {entity_type}s with us so far. Here are your most recent:"]
        for e in entities[:5]:
            status = str(e.summary_fields.get("status", "unknown")).replace("_", " ")
            currency = e.summary_fields.get("currency", "USD")
            total = e.summary_fields.get("total_amount")
            total_str = f", {currency} {total:.2f}" if total is not None else ""
            lines.append(f"- {e.title} ({status}{total_str})")
        return "\n".join(lines)
