"""Multi-agent system.

Phase 1 shipped Protocol-only contracts.

Phase 2 implements the runtime:

* :class:`MemoryBus` (Protocol) + :class:`InMemoryBus` /
  :class:`JsonFileMemoryBus` — durable key/value store passed to every
  step.
* :class:`AgentStep` + :class:`ExecutionDAG` — typed adjacency list
  with cycle detection and topological layering.
* :class:`AgentRunner` — runs one step with bounded retries and
  wallclock timeout.
* :class:`Orchestrator` — schedules a DAG with parallel layers and
  durable replay (re-runs with the same ``run_id`` skip persisted steps).
* :class:`AgentRegistry` — name → factory.

Real agents shipping in Phase 2:

* :class:`RootCauseAgent` — graph-walking, retrieval-aware,
  no-LLM. Real code, real tests.

LLM-backed agents (PatchGenerationAgent, etc.) land on the same
:class:`Agent` Protocol once the ``LLMClient`` Protocol is added.
"""

from .base import AgentResult, AgentStatus
from .memory import InMemoryBus, JsonFileMemoryBus, MemoryBus
from .orchestrator import Orchestrator
from .protocols import Agent, Plan, PlannerAgent, ToolCall
from .registry import AgentRegistry, default_registry
from .root_cause import RootCauseAgent, RootCauseHypothesis
from .runtime import AgentRunner, AgentStep, ExecutionDAG

__all__ = [
    "Agent",
    "AgentRegistry",
    "AgentResult",
    "AgentRunner",
    "AgentStatus",
    "AgentStep",
    "ExecutionDAG",
    "InMemoryBus",
    "JsonFileMemoryBus",
    "MemoryBus",
    "Orchestrator",
    "Plan",
    "PlannerAgent",
    "RootCauseAgent",
    "RootCauseHypothesis",
    "ToolCall",
    "default_registry",
]
