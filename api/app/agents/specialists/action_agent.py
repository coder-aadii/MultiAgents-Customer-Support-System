"""Controlled action proposal specialist; it drafts but never executes mutations."""

from __future__ import annotations

from app.actions.idempotency import IdempotencyEngine
from app.actions.registry import ActionRegistry
from app.agents.base import AgentContext
from app.agents.specialists.base import SpecialistAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus


class ActionProposalSpecialistAgent(SpecialistAgent):
    agent_name = "action_proposal_specialist"
    agent_version = "1.0"
    system_prompt = "phase_6c_action_proposal_specialist_v1"

    def get_capabilities(self) -> list[str]:
        return ["validated_action_proposal"]

    def get_tool_allowlist(self) -> set[str]:
        return set()

    async def investigate(self, context: AgentContext) -> SpecialistFindings:
        if not self.has_verified_session(context):
            return self.unauthenticated(self.agent_name)
        if context.db is None:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                failure_reason="DATABASE_SESSION_REQUIRED",
            )
        action_name = self.payload_string(context, "action_name")
        parameters = self.payload_dict(context, "parameters")
        action = ActionRegistry.get(action_name) if action_name else None
        if action is None or parameters is None:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.failed,
                failure_reason="ACTION_OR_PARAMETERS_INVALID",
            )

        assert context.visitor_session is not None
        customer_id = context.visitor_session.verified_customer_id
        if not customer_id:
            return self.unauthenticated(self.agent_name)
        eligible, message, validated_parameters = await action.validate_preconditions(
            db=context.db,
            conversation_id=context.conversation_id,
            customer_id=customer_id,
            parameters=parameters,
        )
        if not eligible:
            return SpecialistFindings(
                specialist=self.agent_name,
                status=SpecialistStatus.ineligible,
                confidence=1.0,
                recommendations=(message,),
                failure_reason="ACTION_PRECONDITION_FAILED",
            )

        summary, confirmation_phrase = action.generate_confirmation_phrase(customer_id, validated_parameters)
        draft, _is_new = await IdempotencyEngine.create_or_get_draft(
            db=context.db,
            conversation_id=context.conversation_id,
            customer_id=customer_id,
            action_name=action.name,
            risk_level=action.risk_level,
            summary_message=summary,
            required_confirmation_phrase=confirmation_phrase,
            payload=validated_parameters,
        )
        return SpecialistFindings(
            specialist=self.agent_name,
            status=SpecialistStatus.completed,
            confidence=1.0,
            recommendations=(summary, "Customer confirmation is required before this action can execute."),
            action_draft=draft.model_dump(mode="json"),
        )
