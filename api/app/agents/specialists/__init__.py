"""Phase 6C specialist registration and public contracts."""

from __future__ import annotations

from app.agents.registry import AgentRegistry, default_agent_registry
from app.agents.specialists.action_agent import ActionProposalSpecialistAgent
from app.agents.specialists.billing_agent import BillingSpecialistAgent
from app.agents.specialists.business_data_agent import BusinessDataSpecialistAgent
from app.agents.specialists.knowledge_agent import KnowledgeSpecialistAgent
from app.agents.specialists.logistics_agent import LogisticsSpecialistAgent
from app.agents.specialists.resolution_agent import ResolutionSynthesisAgent
from app.agents.specialists.schemas import SpecialistFindings, SpecialistStatus
from app.agents.specialists.universal_agent import UniversalEntitySpecialistAgent

SPECIALIST_AGENT_CLASSES: tuple[type, ...] = (
    KnowledgeSpecialistAgent,
    BusinessDataSpecialistAgent,
    LogisticsSpecialistAgent,
    BillingSpecialistAgent,
    ActionProposalSpecialistAgent,
    ResolutionSynthesisAgent,
    UniversalEntitySpecialistAgent,
)


def register_specialists(registry: AgentRegistry = default_agent_registry) -> None:
    """Register built-in code-owned specialists once per process."""
    for agent_class in SPECIALIST_AGENT_CLASSES:
        if registry.get(agent_class.agent_name) is None:
            registry.register(agent_class)


register_specialists()

__all__ = [
    "ActionProposalSpecialistAgent",
    "BillingSpecialistAgent",
    "BusinessDataSpecialistAgent",
    "KnowledgeSpecialistAgent",
    "LogisticsSpecialistAgent",
    "ResolutionSynthesisAgent",
    "SpecialistFindings",
    "SpecialistStatus",
    "UniversalEntitySpecialistAgent",
    "register_specialists",
]
