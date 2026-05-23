"""Tests for the agent orchestration runtime.

These tests use synthetic agents (no LLM, no graph). They exercise the
runtime invariants: DAG validation, parallel scheduling, retry,
timeout, durable replay.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from repoheal.agents import (
    AgentRegistry,
    AgentResult,
    AgentRunner,
    AgentStatus,
    AgentStep,
    ExecutionDAG,
    InMemoryBus,
    JsonFileMemoryBus,
    Orchestrator,
)


# --- ExecutionDAG ---------------------------------------------------------


def test_dag_topological_layers_simple() -> None:
    dag = ExecutionDAG(
        [
            AgentStep(name="a", agent="dummy"),
            AgentStep(name="b", agent="dummy", depends_on=("a",)),
            AgentStep(name="c", agent="dummy", depends_on=("a",)),
            AgentStep(name="d", agent="dummy", depends_on=("b", "c")),
        ]
    )
    layers = dag.topological_layers()
    assert layers == [["a"], ["b", "c"], ["d"]]


def test_dag_rejects_cycle() -> None:
    with pytest.raises(ValueError, match="cycle"):
        ExecutionDAG(
            [
                AgentStep(name="a", agent="x", depends_on=("b",)),
                AgentStep(name="b", agent="x", depends_on=("a",)),
            ]
        )


def test_dag_rejects_unknown_dependency() -> None:
    with pytest.raises(ValueError, match="unknown"):
        ExecutionDAG([AgentStep(name="a", agent="x", depends_on=("missing",))])


def test_dag_rejects_duplicate_step_name() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ExecutionDAG(
            [
                AgentStep(name="a", agent="x"),
                AgentStep(name="a", agent="y"),
            ]
        )


# --- helpers --------------------------------------------------------------


class _OkAgent:
    """Agent that records calls and returns an OK result with given payload."""

    def __init__(self, name: str, payload: dict[str, Any] | None = None) -> None:
        self._name = name
        self._payload = payload or {}
        self.run_count = 0

    @property
    def name(self) -> str:
        return self._name

    async def run(self, state: dict[str, Any]) -> AgentResult:
        self.run_count += 1
        return AgentResult(
            agent=self._name,
            status=AgentStatus.OK,
            summary="ok",
            output={**self._payload, "saw_inputs": state.get("inputs", {})},
        )


class _FlakyAgent:
    """Fails the first N attempts, then succeeds."""

    def __init__(self, name: str, fails_first: int = 1) -> None:
        self._name = name
        self._fails_first = fails_first
        self.attempts = 0

    @property
    def name(self) -> str:
        return self._name

    async def run(self, state: dict[str, Any]) -> AgentResult:
        self.attempts += 1
        if self.attempts <= self._fails_first:
            raise RuntimeError(f"flake on attempt {self.attempts}")
        return AgentResult(agent=self._name, status=AgentStatus.OK, summary="ok")


class _SlowAgent:
    """Sleeps longer than the runner timeout — must time out."""

    def __init__(self, name: str, sleep: float = 1.0) -> None:
        self._name = name
        self._sleep = sleep

    @property
    def name(self) -> str:
        return self._name

    async def run(self, state: dict[str, Any]) -> AgentResult:
        await asyncio.sleep(self._sleep)
        return AgentResult(agent=self._name, status=AgentStatus.OK, summary="ok")


# --- AgentRunner ---------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_returns_result_on_success() -> None:
    runner = AgentRunner(_OkAgent("ok"))
    result = await runner.run({})
    assert result.status == AgentStatus.OK
    assert result.finished_at is not None


@pytest.mark.asyncio
async def test_runner_retries_flaky_agent() -> None:
    agent = _FlakyAgent("flaky", fails_first=1)
    runner = AgentRunner(agent, max_attempts=3)
    result = await runner.run({})
    assert result.status == AgentStatus.OK
    assert agent.attempts == 2


@pytest.mark.asyncio
async def test_runner_marks_failure_after_max_attempts() -> None:
    agent = _FlakyAgent("never", fails_first=10)
    runner = AgentRunner(agent, max_attempts=2)
    result = await runner.run({})
    assert result.status == AgentStatus.FAILED
    assert result.errors  # populated
    assert agent.attempts == 2


@pytest.mark.asyncio
async def test_runner_times_out_slow_agent() -> None:
    runner = AgentRunner(_SlowAgent("slow", sleep=0.5), max_attempts=1, timeout_seconds=0.05)
    result = await runner.run({})
    assert result.status == AgentStatus.FAILED
    assert "timeout" in (result.errors[0] if result.errors else "")


@pytest.mark.asyncio
async def test_runner_propagates_cancellation() -> None:
    """A CancelledError inside the agent must surface, not be retried."""

    class _Cancellable:
        @property
        def name(self) -> str:
            return "cancellable"

        async def run(self, state: dict[str, Any]) -> AgentResult:
            await asyncio.sleep(0)  # yield once so cancel can land
            raise asyncio.CancelledError

    runner = AgentRunner(_Cancellable(), max_attempts=3)
    with pytest.raises(asyncio.CancelledError):
        await runner.run({})


def test_runner_validates_construction_args() -> None:
    with pytest.raises(ValueError):
        AgentRunner(_OkAgent("x"), max_attempts=0)
    with pytest.raises(ValueError):
        AgentRunner(_OkAgent("x"), timeout_seconds=0)


# --- Orchestrator + replay ------------------------------------------------


def _make_registry(*agents: Any) -> AgentRegistry:
    reg = AgentRegistry()
    for a in agents:
        # bind a in default arg to avoid late-binding in the lambda
        reg.register(a.name, lambda _agent=a, **_kw: _agent)
    return reg


@pytest.mark.asyncio
async def test_orchestrator_runs_dag_in_order() -> None:
    a = _OkAgent("alpha", payload={"k": "alpha"})
    b = _OkAgent("beta", payload={"k": "beta"})
    c = _OkAgent("gamma", payload={"k": "gamma"})
    reg = _make_registry(a, b, c)
    bus = InMemoryBus()
    dag = ExecutionDAG(
        [
            AgentStep(name="s1", agent="alpha"),
            AgentStep(name="s2", agent="beta", depends_on=("s1",)),
            AgentStep(name="s3", agent="gamma", depends_on=("s1",)),
        ]
    )
    orch = Orchestrator(reg, bus, runner_factory=lambda agent: AgentRunner(agent, max_attempts=1, timeout_seconds=5))
    results = await orch.execute(dag, run_id="r1")
    assert {n: r.status for n, r in results.items()} == {
        "s1": AgentStatus.OK,
        "s2": AgentStatus.OK,
        "s3": AgentStatus.OK,
    }
    # All three agents ran exactly once.
    assert (a.run_count, b.run_count, c.run_count) == (1, 1, 1)


@pytest.mark.asyncio
async def test_orchestrator_replays_persisted_steps() -> None:
    a = _OkAgent("alpha")
    b = _OkAgent("beta")
    reg = _make_registry(a, b)
    bus = InMemoryBus()
    dag = ExecutionDAG(
        [
            AgentStep(name="s1", agent="alpha"),
            AgentStep(name="s2", agent="beta", depends_on=("s1",)),
        ]
    )
    orch = Orchestrator(reg, bus, runner_factory=lambda agent: AgentRunner(agent, max_attempts=1, timeout_seconds=5))
    # First run: both execute.
    await orch.execute(dag, run_id="rerun")
    assert a.run_count == 1
    assert b.run_count == 1

    # Second run with the same run_id: nothing should re-execute.
    await orch.execute(dag, run_id="rerun")
    assert a.run_count == 1
    assert b.run_count == 1


@pytest.mark.asyncio
async def test_orchestrator_handles_unknown_agent() -> None:
    reg = AgentRegistry()  # empty
    bus = InMemoryBus()
    dag = ExecutionDAG([AgentStep(name="s1", agent="missing")])
    orch = Orchestrator(reg, bus)
    results = await orch.execute(dag, run_id="x")
    assert results["s1"].status == AgentStatus.FAILED


@pytest.mark.asyncio
async def test_orchestrator_runs_independent_steps_in_parallel() -> None:
    """Two steps in the same layer that each sleep 0.1s should finish in
    ~0.1s total, not ~0.2s, because asyncio.gather schedules them."""
    import time

    class _Sleeper:
        def __init__(self, name: str) -> None:
            self._name = name
            self.run_count = 0

        @property
        def name(self) -> str:
            return self._name

        async def run(self, state: dict[str, Any]) -> AgentResult:
            self.run_count += 1
            await asyncio.sleep(0.1)
            return AgentResult(agent=self._name, status=AgentStatus.OK, summary="ok")

    s1 = _Sleeper("s1")
    s2 = _Sleeper("s2")
    reg = _make_registry(s1, s2)
    bus = InMemoryBus()
    dag = ExecutionDAG(
        [
            AgentStep(name="a", agent="s1"),
            AgentStep(name="b", agent="s2"),
        ]
    )
    orch = Orchestrator(reg, bus, runner_factory=lambda agent: AgentRunner(agent, max_attempts=1, timeout_seconds=5))

    start = time.perf_counter()
    await orch.execute(dag, run_id="par")
    elapsed = time.perf_counter() - start

    # Generous bound to avoid CI flake; sequential would be ≥0.2s.
    assert elapsed < 0.18


# --- MemoryBus ------------------------------------------------------------


def test_in_memory_bus_round_trips_dict() -> None:
    bus = InMemoryBus()
    bus.put("k", {"x": 1, "y": [1, 2, 3]})
    assert bus.get("k") == {"x": 1, "y": [1, 2, 3]}
    assert bus.get("missing") is None
    assert "k" in bus.keys()


def test_json_file_bus_persists_and_reloads(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    bus = JsonFileMemoryBus(p)
    bus.put("a", 1)
    bus.put("b", {"nested": True})

    # File exists and is JSON.
    import json as _json
    raw = _json.loads(p.read_text(encoding="utf-8"))
    assert raw == {"a": 1, "b": {"nested": True}}

    # Reload in a fresh instance.
    bus2 = JsonFileMemoryBus(p)
    assert bus2.get("a") == 1
    assert bus2.get("b") == {"nested": True}


def test_json_file_bus_handles_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "does_not_exist.json"
    bus = JsonFileMemoryBus(p)
    assert bus.get("anything") is None
    bus.put("x", 1)
    assert p.exists()


def test_in_memory_bus_snapshot_round_trip() -> None:
    bus = InMemoryBus()
    bus.put("a", 1)
    bus.put("b", "two")
    snap = bus.snapshot()

    bus2 = InMemoryBus()
    bus2.load(snap)
    assert bus2.get("a") == 1
    assert bus2.get("b") == "two"


# --- AgentRegistry --------------------------------------------------------


def test_registry_create_yields_factory_instance() -> None:
    class _A:
        def __init__(self, x: int = 0) -> None:
            self.x = x

        @property
        def name(self) -> str:
            return "A"

        async def run(self, state: dict[str, Any]) -> AgentResult:
            return AgentResult(agent="A", status=AgentStatus.OK, summary="ok")

    reg = AgentRegistry()
    reg.register("A", _A)
    inst = reg.create("A", x=42)
    assert inst.x == 42  # type: ignore[attr-defined]


def test_registry_unknown_agent_raises() -> None:
    reg = AgentRegistry()
    with pytest.raises(KeyError):
        reg.create("nope")


def test_registry_rejects_duplicate() -> None:
    reg = AgentRegistry()
    reg.register("a", lambda **kw: object())  # type: ignore[arg-type, return-value]
    with pytest.raises(ValueError):
        reg.register("a", lambda **kw: object())  # type: ignore[arg-type, return-value]
