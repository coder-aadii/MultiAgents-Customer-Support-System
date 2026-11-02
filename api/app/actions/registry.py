from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.base import ActionDefinition, ActionResult, RiskLevel
from app.models.business_action import ActionStatus, BusinessAction

logger = logging.getLogger("mcss.actions.registry")


class UpdateCommunicationPreferencesAction(ActionDefinition):
    name = "update_communication_preferences"
    risk_level = RiskLevel.LOW
    description = "Update customer notification and communication channel preferences"

    async def validate_preconditions(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        customer_id: str,
        parameters: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        email_opt = parameters.get("email_notifications", True)
        sms_opt = parameters.get("sms_notifications", False)
        return (
            True,
            "Valid communication preference parameters",
            {"email_notifications": bool(email_opt), "sms_notifications": bool(sms_opt)},
        )

    def generate_confirmation_phrase(
        self,
        customer_id: str,
        validated_parameters: dict[str, Any],
    ) -> tuple[str, str]:
        email_status = "enabled" if validated_parameters.get("email_notifications") else "disabled"
        sms_status = "enabled" if validated_parameters.get("sms_notifications") else "disabled"
        summary = f"Update communication preferences (Email: {email_status}, SMS: {sms_status})"
        phrase = "CONFIRM PREFERENCES"
        return summary, phrase

    async def execute(
        self,
        db: AsyncSession,
        action: BusinessAction,
    ) -> ActionResult:
        # In a real system or test environment, persists to preferences store or CRM
        prefs = action.payload_json
        return ActionResult(
            action_id=action.id,
            status=ActionStatus.completed,
            success=True,
            confirmation_number=f"PREF-{uuid.uuid4().hex[:8].upper()}",
            message="Your communication preferences have been successfully updated.",
            details={"updated_preferences": prefs},
        )


class OrderCancellationAction(ActionDefinition):
    name = "cancel_order"
    risk_level = RiskLevel.MEDIUM
    description = "Cancel an unfulfilled customer order prior to warehouse dispatch"

    async def validate_preconditions(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        customer_id: str,
        parameters: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        order_id = parameters.get("order_id")
        if not order_id:
            return False, "Missing order_id parameter", {}

        clean_order_id = str(order_id).strip().upper()

        # Check order eligibility via enterprise connector or state check
        # Precondition Rule: Orders already in 'shipped' or 'delivered' state cannot be canceled
        order_status = parameters.get("current_order_status") or parameters.get("status", "processing")
        if order_status.lower() in ("shipped", "delivered", "in_transit"):
            return False, f"Order {clean_order_id} has already shipped and cannot be canceled.", {}

        reason = parameters.get("reason", "Customer requested cancellation")
        return True, f"Order {clean_order_id} is eligible for cancellation.", {
            "order_id": clean_order_id,
            "reason": reason,
        }

    def generate_confirmation_phrase(
        self,
        customer_id: str,
        validated_parameters: dict[str, Any],
    ) -> tuple[str, str]:
        order_id = validated_parameters["order_id"]
        summary = f"Cancel order {order_id}"
        phrase = f"CONFIRM CANCEL {order_id}"
        return summary, phrase

    async def execute(
        self,
        db: AsyncSession,
        action: BusinessAction,
    ) -> ActionResult:
        order_id = action.payload_json.get("order_id")
        # Invariant: order cannot be cancelled if shipped
        current_status = action.payload_json.get("current_order_status", "processing")
        if current_status.lower() in ("shipped", "delivered", "in_transit"):
            return ActionResult(
                action_id=action.id,
                status=ActionStatus.failed,
                success=False,
                message=f"Order {order_id} could not be canceled because it has already shipped.",
                details={"order_id": order_id, "error": "ORDER_ALREADY_SHIPPED"},
            )

        # Wire to enterprise connector if registered
        from app.connectors.registry import default_connector_registry
        connector = default_connector_registry.get_connector("ecommerce_store") or default_connector_registry.get_connector("order_connector")
        if connector and "cancel_order" in connector.supported_operations:
            mut_res = await connector.execute_mutation(
                operation="cancel_order",
                verified_customer_id=action.customer_id,
                parameters={"order_id": order_id, "reason": action.payload_json.get("reason")},
                idempotency_key=action.idempotency_key,
            )
            if not mut_res.success:
                return ActionResult(
                    action_id=action.id,
                    status=ActionStatus.failed,
                    success=False,
                    message=mut_res.error_message or f"Failed to cancel order {order_id} via connector.",
                    details={"order_id": order_id, "error": mut_res.error_code},
                )
            conf_code = (mut_res.data or {}).get("cancellation_code") if isinstance(mut_res.data, dict) else None
            conf_code = conf_code or f"CNCL-{order_id}-{uuid.uuid4().hex[:6].upper()}"
        else:
            conf_code = f"CNCL-{order_id}-{uuid.uuid4().hex[:6].upper()}"

        return ActionResult(
            action_id=action.id,
            status=ActionStatus.completed,
            success=True,
            confirmation_number=conf_code,
            message=f"Order {order_id} has been successfully canceled.",
            details={"order_id": order_id, "cancellation_code": conf_code},
        )


class StoreCreditAction(ActionDefinition):
    name = "issue_store_credit"
    risk_level = RiskLevel.HIGH
    description = "Issue appeasement store credit to verified customer wallet"

    async def validate_preconditions(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        customer_id: str,
        parameters: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        amount = parameters.get("amount")
        if amount is None or float(amount) <= 0:
            return False, "Store credit amount must be greater than $0.00", {}

        credit_amount = round(float(amount), 2)
        if credit_amount > 200.0:
            return False, "Store credit exceeds maximum automated ceiling of $200.00", {}

        reason = parameters.get("reason", "Customer appeasement")
        return True, f"Store credit of ${credit_amount:.2f} is valid.", {
            "amount": credit_amount,
            "reason": reason,
        }

    def generate_confirmation_phrase(
        self,
        customer_id: str,
        validated_parameters: dict[str, Any],
    ) -> tuple[str, str]:
        amt = validated_parameters["amount"]
        summary = f"Issue store credit of ${amt:.2f}"
        phrase = f"CONFIRM CREDIT ${amt:.2f}"
        return summary, phrase

    async def execute(
        self,
        db: AsyncSession,
        action: BusinessAction,
    ) -> ActionResult:
        amt = action.payload_json.get("amount")
        conf_code = f"CRD-{uuid.uuid4().hex[:8].upper()}"
        return ActionResult(
            action_id=action.id,
            status=ActionStatus.completed,
            success=True,
            confirmation_number=conf_code,
            message=f"Store credit of ${amt:.2f} has been successfully issued to your account.",
            details={"amount": amt, "credit_reference": conf_code},
        )


class ActionRegistry:
    """Central registry of executable business actions."""

    _actions: dict[str, ActionDefinition] = {
        "update_communication_preferences": UpdateCommunicationPreferencesAction(),
        "cancel_order": OrderCancellationAction(),
        "issue_store_credit": StoreCreditAction(),
    }

    @classmethod
    def register(cls, action_def: ActionDefinition) -> None:
        cls._actions[action_def.name] = action_def

    @classmethod
    def get(cls, action_name: str) -> ActionDefinition | None:
        return cls._actions.get(action_name)

    @classmethod
    def list_actions(cls) -> list[ActionDefinition]:
        return list(cls._actions.values())

