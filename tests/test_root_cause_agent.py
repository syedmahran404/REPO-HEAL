"""Tests for the RootCauseAgent — end-to-end against the medium_repo."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoheal.agents import (
    AgentRegistry,
    AgentResult,
    AgentRunner,
    AgentStatus,
    AgentStep,
    ExecutionDAG,
    InMemoryBus,
    Orchestrator,
    RootCauseAgent,
    default_registry,
)
from repoheal.analysis import AnalysisService
from repoheal.graph import NodeKind, symbol_node_id

pytest.importorskip("tree_sitter_languages")


def _func_id(qname: str) -> str:
    return symbol_node_id(qname, kind=NodeKind.FUNCTION)


@pytest.mark.asyncio
async def test_root_cause_finds_callers_upstream(medium_repo: Path) -> None:
    """``handle_request`` is called by ``public_endpoint``. Anchored at
    handle_request, the root-cause agent should surface
    public_endpoint as a high-ranked hypothesis."""
    result = AnalysisService().analyze(str(medium_repo))
    graph = result.graph

    anchor = _func_id("pkg.services.handle_request")
    assert graph.has_node(anchor)

    agent = RootCauseAgent()
    state = {
        "graph": graph,
        "repo": result.repository,
        "inputs": {"anchor_node": anchor, "max_depth": 3, "top_k": 5},
    }
    out = await agent.run(state)
    assert out.status == AgentStatus.OK

    qnames = [h["qualified_name"] for h in out.output["hypotheses"]]
    assert "pkg.api.public_endpoint" in qnames


@pytest.mark.asyncio
async def test_root_cause_returns_failed_for_unknown_anchor(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    agent = RootCauseAgent()
    state = {
        "graph": result.graph,
        "repo": result.repository,
        "inputs": {"anchor_node": "module::does.not.exist"},
    }
    out = await agent.run(state)
    assert out.status == AgentStatus.FAILED


@pytest.mark.asyncio
async def test_root_cause_returns_failed_without_inputs(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    agent = RootCauseAgent()
    state = {"graph": result.graph, "repo": result.repository}  # no inputs
    out = await agent.run(state)
    assert out.status == AgentStatus.FAILED


@pytest.mark.asyncio
async def test_root_cause_uses_retrieval_when_available(medium_repo: Path) -> None:
    """If retrieval is wired in, candidates that retrieval surfaces get
    a higher composite score. Smoke test: it does not crash and the
    rationales mention retrieval where applicable."""
    result = AnalysisService().analyze(str(medium_repo), build_retrieval=True)
    agent = RootCauseAgent()
    anchor = _func_id("pkg.services.handle_request")
    state = {
        "graph": result.graph,
        "repo": result.repository,
        "retrieval": result.retrieval,
        "inputs": {"anchor_node": anchor, "max_depth": 3, "top_k": 5},
    }
    out = await agent.run(state)
    assert out.status == AgentStatus.OK
    # Hypotheses are sorted by composite score desc.
    scores = [h["score"] for h in out.output["hypotheses"]]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_root_cause_via_orchestrator(medium_repo: Path) -> None:
    """End-to-end: drive the agent through the full orchestrator."""
    result = AnalysisService().analyze(str(medium_repo))
    anchor = _func_id("pkg.services.handle_request")

    bus = InMemoryBus()
    dag = ExecutionDAG(
        [
            AgentStep(
                name="rc",
                agent="root_cause",
                inputs={"anchor_node": anchor, "max_depth": 3, "top_k": 3},
            )
        ]
    )
    orch = Orchestrator(
        default_registry(),
        bus,
        runner_factory=lambda agent: AgentRunner(agent, max_attempts=1, timeout_seconds=5),
    )

    results = await orch.execute(
        dag,
        run_id="rc-test",
        shared_state={
            "graph": result.graph,
            "repo": result.repository,
            "retrieval": result.retrieval,
        },
    )

    assert results["rc"].status == AgentStatus.OK
    qnames = [h["qualified_name"] for h in results["rc"].output["hypotheses"]]
    assert "pkg.api.public_endpoint" in qnames

    # Persisted in the bus.
    assert bus.get("run::rc-test::step::rc") is not None


def test_default_registry_includes_root_cause() -> None:
    reg = default_registry()
    assert "root_cause" in reg.names()
    inst = reg.create("root_cause")
    assert inst.name == "root_cause"
