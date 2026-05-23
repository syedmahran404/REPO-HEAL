"""Tests for the knowledge graph subsystem."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoheal.exceptions import NodeNotFoundError
from repoheal.graph import (
    EdgeKind,
    GraphBuilder,
    ImpactAnalysis,
    NetworkXGraphBackend,
    NodeKind,
    file_node_id,
    module_node_id,
)
from repoheal.ingestion import IngestionService
from repoheal.intelligence import TreeSitterParser

pytest.importorskip("tree_sitter_languages")


# --- backend primitives ---------------------------------------------------


def test_networkx_backend_basic_ops() -> None:
    g = NetworkXGraphBackend()
    g.add_node("a", kind="x")
    g.add_node("b", kind="x")
    g.add_edge("a", "b", kind="r")

    assert g.has_node("a")
    assert g.node_count() == 2
    assert g.edge_count() == 1
    assert list(g.neighbors("a", direction="out")) == ["b"]
    assert list(g.neighbors("b", direction="in")) == ["a"]
    assert list(g.neighbors("a", kind="r")) == ["b"]
    assert list(g.neighbors("a", kind="other")) == []


def test_networkx_backend_neighbors_unknown_node_raises() -> None:
    g = NetworkXGraphBackend()
    with pytest.raises(NodeNotFoundError):
        list(g.neighbors("missing"))


def test_networkx_backend_find_cycles_simple() -> None:
    g = NetworkXGraphBackend()
    g.add_node("a")
    g.add_node("b")
    g.add_node("c")  # standalone, no cycles
    g.add_edge("a", "b", kind="imports")
    g.add_edge("b", "a", kind="imports")
    cycles = g.find_cycles(kind="imports")
    assert len(cycles) == 1
    assert set(cycles[0]) == {"a", "b"}


def test_networkx_backend_find_cycles_filters_by_kind() -> None:
    g = NetworkXGraphBackend()
    g.add_edge("a", "b", kind="calls")
    g.add_edge("b", "a", kind="calls")
    assert g.find_cycles(kind="imports") == []
    assert g.find_cycles(kind="calls") != []


def test_networkx_backend_to_dict_round_trips() -> None:
    g = NetworkXGraphBackend()
    g.add_node("a", kind="x")
    g.add_edge("a", "a", kind="self")
    d = g.to_dict()
    assert {n["id"] for n in d["nodes"]} == {"a"}
    assert d["edges"][0]["src"] == "a"


# --- builder + queries (end-to-end on the fixture) ------------------------


def _build(tiny_repo: Path) -> tuple[NetworkXGraphBackend, list]:
    repo = IngestionService().ingest(tiny_repo)
    parser = TreeSitterParser()
    parsed = []
    for f in repo.files:
        if f.is_binary:
            continue
        if f.size_bytes > 2_000_000:
            continue
        parsed.append(parser.parse_path(repo.root, f))
    g = NetworkXGraphBackend()
    GraphBuilder().build(repo, parsed, g)
    return g, parsed


def test_graph_builder_creates_module_nodes(tiny_repo: Path) -> None:
    g, _ = _build(tiny_repo)
    assert g.has_node(module_node_id("pkg.a"))
    assert g.has_node(module_node_id("pkg.b"))
    assert g.has_node(module_node_id("pkg.standalone"))


def test_graph_builder_creates_file_nodes(tiny_repo: Path) -> None:
    g, _ = _build(tiny_repo)
    assert g.has_node(file_node_id("pkg/a.py"))


def test_graph_builder_emits_imports_cycle(tiny_repo: Path) -> None:
    g, _ = _build(tiny_repo)
    cycles = g.find_cycles(kind=EdgeKind.IMPORTS.value)
    # We expect at least the pkg.a / pkg.b cycle.
    cycle_qnames = {tuple(sorted(c)) for c in cycles}
    a_id = module_node_id("pkg.a")
    b_id = module_node_id("pkg.b")
    assert any(a_id in c and b_id in c for c in cycle_qnames)


def test_impact_analysis_downstream_finds_cycle_partner(tiny_repo: Path) -> None:
    g, _ = _build(tiny_repo)
    impact = ImpactAnalysis(g)
    a_id = module_node_id("pkg.a")
    b_id = module_node_id("pkg.b")
    assert b_id in impact.downstream(a_id)
    # And upstream is symmetric for a 2-cycle.
    assert a_id in impact.upstream(b_id)


def test_impact_analysis_returns_empty_for_unknown_node(tiny_repo: Path) -> None:
    g, _ = _build(tiny_repo)
    impact = ImpactAnalysis(g)
    assert impact.downstream("module::nope") == set()


def test_node_kind_and_edge_kind_values_are_stable_strings() -> None:
    # If we ever change these, every persisted graph and every
    # node-id-using consumer breaks. Lock them down.
    assert NodeKind.MODULE.value == "module"
    assert NodeKind.FILE.value == "file"
    assert EdgeKind.IMPORTS.value == "imports"
    assert EdgeKind.CONTAINS.value == "contains"
