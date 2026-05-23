"""AgentRegistry — name → factory, used by the orchestrator.

We register **factories** (callables returning an Agent) rather than
instances so each step gets a fresh agent. This lets us inject step-
specific configuration via factory keyword arguments without polluting
agent instances with state from previous steps.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .protocols import Agent


class AgentRegistry:
    """Holds agent factories by name."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., Agent]] = {}

    def register(self, name: str, factory: Callable[..., Agent]) -> None:
        if name in self._factories:
            raise ValueError(f"agent {name!r} already registered")
        self._factories[name] = factory

    def has(self, name: str) -> bool:
        return name in self._factories

    def names(self) -> list[str]:
        return list(self._factories)

    def create(self, name: str, **kwargs: Any) -> Agent:
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise KeyError(
                f"unknown agent: {name!r} (registered: {sorted(self._factories)})"
            ) from exc
        return factory(**kwargs)


def default_registry() -> AgentRegistry:
    """Construct the canonical agent registry.

    Phase 2 ships one real, non-LLM agent: ``root_cause``. LLM-backed
    agents register here once the ``LLMClient`` Protocol lands.
    """
    from .root_cause import RootCauseAgent

    reg = AgentRegistry()
    reg.register("root_cause", lambda **kw: RootCauseAgent(**kw))
    return reg


__all__ = ["AgentRegistry", "default_registry"]
