"""Per-step execution primitives: AgentStep, ExecutionDAG, AgentRunner.

* :class:`AgentStep` — declarative description of one step in the DAG.
* :class:`ExecutionDAG` — typed adjacency list with cycle detection
  and topological layering for parallel scheduling.
* :class:`AgentRunner` — runs ONE step with bounded retries, wallclock
  timeout, structured exception capture, and per-attempt telemetry.

ADR-0004 explains why we built this rather than adopting LangGraph.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..logging import get_logger
from ..obs.tracing import traced
from .base import AgentResult, AgentStatus
from .protocols import Agent

_log = get_logger(__name__)


# =============================================================================
# DAG primitives
# =============================================================================


@dataclass(frozen=True)
class AgentStep:
    """One step in an :class:`ExecutionDAG`.

    A step references an agent by **name** (looked up in the registry)
    and carries its inputs as a JSON-shaped dict. ``depends_on`` is the
    set of step names this step waits for.
    """

    name: str
    agent: str
    inputs: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()


class ExecutionDAG:
    """A directed acyclic graph of agent steps.

    Construction validates two invariants:

    1. Every dependency references a step that is also in the DAG.
    2. The graph is acyclic.

    :meth:`topological_layers` returns groups of steps that can run in
    parallel (Kahn's algorithm)."""

    def __init__(self, steps: list[AgentStep]) -> None:
        self._steps: dict[str, AgentStep] = {}
        for s in steps:
            if s.name in self._steps:
                raise ValueError(f"duplicate step name: {s.name!r}")
            self._steps[s.name] = s
        self._validate()

    # ------------------------------------------------------------------

    @property
    def steps(self) -> dict[str, AgentStep]:
        return dict(self._steps)

    def step(self, name: str) -> AgentStep:
        return self._steps[name]

    def topological_layers(self) -> list[list[str]]:
        """Return layers of step names; same-layer steps can run in parallel."""
        in_degree: dict[str, int] = {n: 0 for n in self._steps}
        successors: dict[str, list[str]] = defaultdict(list)
        for name, step in self._steps.items():
            for dep in step.depends_on:
                in_degree[name] += 1
                successors[dep].append(name)

        layers: list[list[str]] = []
        ready = sorted(n for n, d in in_degree.items() if d == 0)
        while ready:
            layers.append(ready)
            next_ready: list[str] = []
            for n in ready:
                for succ in successors[n]:
                    in_degree[succ] -= 1
                    if in_degree[succ] == 0:
                        next_ready.append(succ)
            ready = sorted(next_ready)

        # If we missed any node it's because of a cycle (shouldn't happen
        # because _validate runs at construction, but defensive).
        if sum(len(layer) for layer in layers) != len(self._steps):
            raise ValueError("cycle detected in execution DAG (post-construction)")
        return layers

    # ------------------------------------------------------------------

    def _validate(self) -> None:
        # Reference integrity.
        for name, step in self._steps.items():
            for dep in step.depends_on:
                if dep not in self._steps:
                    raise ValueError(
                        f"step {name!r} depends on unknown step {dep!r}"
                    )

        # Acyclicity via DFS.
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._steps}

        def visit(n: str, stack: list[str]) -> None:
            if color[n] == GRAY:
                cycle_path = " -> ".join(stack[stack.index(n):] + [n])
                raise ValueError(f"cycle in DAG: {cycle_path}")
            if color[n] == BLACK:
                return
            color[n] = GRAY
            stack.append(n)
            for dep in self._steps[n].depends_on:
                visit(dep, stack)
            stack.pop()
            color[n] = BLACK

        for n in self._steps:
            visit(n, [])


# =============================================================================
# Runner
# =============================================================================


class AgentRunner:
    """Execute one agent with retries, timeout, and telemetry.

    The runner is the unit of failure isolation. An agent that raises
    is caught here; an agent that hangs is timed out here; an agent
    that succeeds returns its :class:`AgentResult` unchanged.
    """

    def __init__(
        self,
        agent: Agent,
        *,
        max_attempts: int = 2,
        timeout_seconds: float = 60.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._agent = agent
        self._max_attempts = max_attempts
        self._timeout = timeout_seconds

    # ------------------------------------------------------------------

    async def run(self, state: dict[str, Any]) -> AgentResult:
        last_error: str | None = None
        started = datetime.now(timezone.utc)
        wallclock_start = time.perf_counter()

        with traced(
            "agent.run",
            agent=self._agent.name,
            max_attempts=self._max_attempts,
            timeout_seconds=self._timeout,
        ):
            for attempt in range(1, self._max_attempts + 1):
                attempt_started = time.perf_counter()
                _log.info(
                    "agent.attempt.start",
                    agent=self._agent.name,
                    attempt=attempt,
                    max_attempts=self._max_attempts,
                )
                try:
                    with traced("agent.attempt", agent=self._agent.name, attempt=attempt):
                        result = await asyncio.wait_for(
                            self._agent.run(state),
                            timeout=self._timeout,
                        )
                except asyncio.TimeoutError:
                    last_error = f"timeout after {self._timeout}s"
                    _log.warning(
                        "agent.attempt.timeout",
                        agent=self._agent.name,
                        attempt=attempt,
                        timeout=self._timeout,
                    )
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    _log.warning(
                        "agent.attempt.error",
                        agent=self._agent.name,
                        attempt=attempt,
                        error=last_error,
                    )
                    continue

                duration_ms = (time.perf_counter() - attempt_started) * 1000.0
                _log.info(
                    "agent.attempt.ok",
                    agent=self._agent.name,
                    attempt=attempt,
                    status=result.status.value,
                    duration_ms=round(duration_ms, 1),
                )
                return self._with_timing(result, started)

            total_ms = (time.perf_counter() - wallclock_start) * 1000.0
            _log.error(
                "agent.all_attempts_failed",
                agent=self._agent.name,
                attempts=self._max_attempts,
                error=last_error,
                duration_ms=round(total_ms, 1),
            )
            return AgentResult(
                agent=self._agent.name,
                status=AgentStatus.FAILED,
                summary=f"failed after {self._max_attempts} attempts: {last_error}",
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                errors=(last_error,) if last_error else (),
            )

    # ------------------------------------------------------------------

    def _with_timing(self, result: AgentResult, started: datetime) -> AgentResult:
        if result.finished_at is not None:
            return result
        # Re-emit with finished_at set; AgentResult is a Pydantic model.
        return result.model_copy(update={"finished_at": datetime.now(timezone.utc)})


__all__ = ["AgentRunner", "AgentStep", "ExecutionDAG"]
