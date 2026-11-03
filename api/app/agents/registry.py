"""Declarative registry for code-owned agent implementations."""

from __future__ import annotations

from collections.abc import Iterator

from app.agents.base import BaseAgent
from app.agents.schemas import AgentDefinition


class AgentRegistrationError(ValueError):
    pass


class AgentRegistry:
    """Process-local singleton registry; registration is intentionally code-only."""

    _instance: AgentRegistry | None = None

    def __init__(self) -> None:
        self._agents: dict[str, type[BaseAgent]] = {}
        self._definitions: dict[str, AgentDefinition] = {}
        self._capability_owners: dict[str, str] = {}

    @classmethod
    def instance(cls) -> AgentRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, agent_class: type[BaseAgent]) -> type[BaseAgent]:
        if not isinstance(agent_class, type) or not issubclass(agent_class, BaseAgent):
            raise AgentRegistrationError("only BaseAgent subclasses can be registered")

        name = agent_class.agent_name
        if name in self._agents:
            raise AgentRegistrationError(f"agent '{name}' is already registered")
        if not name or len(name) > 64:
            raise AgentRegistrationError("agent_name must be between 1 and 64 characters")

        agent = agent_class()
        capabilities = tuple(agent.get_capabilities())
        allowlist = agent.get_tool_allowlist()
        if not capabilities or any(not capability.strip() for capability in capabilities):
            raise AgentRegistrationError("agents must declare non-empty capabilities")
        if len(set(capabilities)) != len(capabilities):
            raise AgentRegistrationError("agent capability list contains duplicates")
        if any(not isinstance(tool, str) or not tool.strip() for tool in allowlist):
            raise AgentRegistrationError("tool allowlist contains an invalid tool name")
        collisions = [
            capability
            for capability in capabilities
            if capability in self._capability_owners
        ]
        if collisions:
            raise AgentRegistrationError(
                f"capabilities already owned by '{self._capability_owners[collisions[0]]}': {collisions[0]}"
            )

        definition = AgentDefinition(
            name=name,
            version=agent_class.agent_version,
            capabilities=capabilities,
            tool_allowlist=frozenset(allowlist),
        )
        self._agents[name] = agent_class
        self._definitions[name] = definition
        self._capability_owners.update(dict.fromkeys(capabilities, name))
        return agent_class

    def get(self, agent_name: str) -> type[BaseAgent] | None:
        return self._agents.get(agent_name)

    def get_definition(self, agent_name: str) -> AgentDefinition | None:
        return self._definitions.get(agent_name)

    def list_agents(self) -> list[AgentDefinition]:
        return [self._definitions[name] for name in sorted(self._definitions)]

    def __iter__(self) -> Iterator[AgentDefinition]:
        return iter(self.list_agents())


default_agent_registry = AgentRegistry.instance()
