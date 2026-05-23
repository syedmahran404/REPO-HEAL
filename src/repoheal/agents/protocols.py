"""Agent and planner Protocols."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .base import AgentResult


class ToolCall(BaseModel):
    """A request from an agent to invoke a tool."""

    model_config = ConfigDict(frozen=True)
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    """A planner's output: an ordered list of agent invocations.

    Phase 6 will extend this to a DAG; for now a simple sequence is
    enough to express the issue→PR happy path."""

    model_config = ConfigDict(frozen=True)
    steps: tuple[str, ...]  # agent names, in order
    rationale: str = ""


@runtime_checkable
class Agent(Protocol):
    """Single specialised agent."""

    @property
    def name(self) -> str: ...
    async def run(self, state: dict[str, Any]) -> AgentResult: ...


@runtime_checkable
class PlannerAgent(Protocol):
    """Plan a sequence of agent invocations."""

    async def plan(self, goal: str, state: dict[str, Any]) -> Plan: ...


# Hint for Phase 6: the orchestrator owns ``state``, validates it
# against a schema, persists it after each agent step, and resumes from
# the last persisted state on restart. That state machine is the
# missing piece, not the agents themselves.
_ = (Sequence,)  # placate unused-import linters; Sequence is the next-PR shape
