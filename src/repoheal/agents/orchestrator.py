"""Orchestrator — runs a DAG of agent steps with durable replay.

Lifecycle of an :meth:`Orchestrator.execute` call:

1. Compute topological layers via :meth:`ExecutionDAG.topological_layers`.
2. For each layer:
   * For each step name, check the bus for a persisted result under
     ``run::{run_id}::step::{step_name}``. If present, re-hydrate and
     skip execution (idempotent replay).
   * Build the per-step state dict (shared refs from the caller +
     step-specific inputs + the bus + previous results).
   * Run the un-completed steps in parallel via ``asyncio.gather``.
   * Persist each result to the bus before moving on.
3. Return ``{step_name: AgentResult}``.

The persistence guarantee is *durable replay*: a process crash mid-run
followed by a re-run with the same ``run_id`` replays from the last
persisted step. State stored in the bus must therefore be JSON-shaped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..logging import get_logger
from .base import AgentResult, AgentStatus
from .memory import MemoryBus
from .registry import AgentRegistry
from .runtime import AgentRunner, ExecutionDAG

_log = get_logger(__name__)


RunnerFactory = Callable[..., AgentRunner]


class Orchestrator:
    """Schedule and run an :class:`ExecutionDAG`."""

    def __init__(
        self,
        registry: AgentRegistry,
        bus: MemoryBus,
        *,
        runner_factory: RunnerFactory | None = None,
    ) -> None:
        self._registry = registry
        self._bus = bus
        # Default: build a fresh AgentRunner with default timeout/retries
        # for each step. Tests inject their own factory to control these.
        self._runner_factory: RunnerFactory = runner_factory or AgentRunner

    # ------------------------------------------------------------------

    async def execute(
        self,
        dag: ExecutionDAG,
        *,
        run_id: str,
        shared_state: dict[str, Any] | None = None,
    ) -> dict[str, AgentResult]:
        """Run the DAG. ``shared_state`` carries non-persistable refs
        (graph, repo, retrieval) into every step.

        Re-running with the same ``run_id`` replays persisted steps."""
        shared = dict(shared_state or {})
        results: dict[str, AgentResult] = {}

        layers = dag.topological_layers()
        _log.info(
            "orchestrator.start",
            run_id=run_id,
            steps=len(dag.steps),
            layers=len(layers),
        )

        for layer_idx, layer in enumerate(layers):
            tasks: list[asyncio.Task[AgentResult]] = []
            names_in_order: list[str] = []

            for step_name in layer:
                cached = self._load_persisted(run_id, step_name)
                if cached is not None:
                    _log.info(
                        "orchestrator.step.replay",
                        run_id=run_id,
                        step=step_name,
                        status=cached.status.value,
                    )
                    results[step_name] = cached
                    continue

                step = dag.step(step_name)
                if not self._registry.has(step.agent):
                    results[step_name] = AgentResult(
                        agent=step.agent,
                        status=AgentStatus.FAILED,
                        summary=f"unknown agent: {step.agent!r}",
                        errors=(f"agent not registered: {step.agent}",),
                    )
                    self._persist(run_id, step_name, results[step_name])
                    continue

                agent = self._registry.create(step.agent)
                runner = self._runner_factory(agent)
                step_state = self._build_step_state(step, shared, results)
                tasks.append(asyncio.create_task(runner.run(step_state), name=step_name))
                names_in_order.append(step_name)

            if not tasks:
                continue

            layer_results = await asyncio.gather(*tasks, return_exceptions=False)
            for name, result in zip(names_in_order, layer_results, strict=True):
                results[name] = result
                self._persist(run_id, name, result)
                _log.info(
                    "orchestrator.step.done",
                    run_id=run_id,
                    layer=layer_idx,
                    step=name,
                    status=result.status.value,
                )

        _log.info("orchestrator.done", run_id=run_id, steps=len(results))
        return results

    # ------------------------------------------------------------------

    def _build_step_state(
        self,
        step: "AgentStep",  # noqa: F821 forward ref (defined in runtime.py)
        shared: dict[str, Any],
        prior_results: dict[str, AgentResult],
    ) -> dict[str, Any]:
        """Compose the per-step state dict.

        Layered (later wins on collision so explicit per-step inputs
        override shared defaults)."""
        return {
            **shared,
            "bus": self._bus,
            "step_name": step.name,
            "inputs": dict(step.inputs),
            "step_results": {
                k: v.model_dump(mode="json") for k, v in prior_results.items()
            },
        }

    def _persist(self, run_id: str, step_name: str, result: AgentResult) -> None:
        key = self._step_key(run_id, step_name)
        self._bus.put(key, result.model_dump(mode="json"))

    def _load_persisted(self, run_id: str, step_name: str) -> AgentResult | None:
        raw = self._bus.get(self._step_key(run_id, step_name))
        if raw is None:
            return None
        try:
            return AgentResult.model_validate(raw)
        except Exception as exc:
            _log.warning(
                "orchestrator.persist.invalid",
                run_id=run_id,
                step=step_name,
                error=str(exc),
            )
            return None

    @staticmethod
    def _step_key(run_id: str, step_name: str) -> str:
        return f"run::{run_id}::step::{step_name}"


__all__ = ["Orchestrator"]
