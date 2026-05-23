"""Tests for Phase 2 graph extensions: CALLS, INHERITS, REFERENCES edges."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoheal.analysis import AnalysisService
from repoheal.graph import EdgeKind, NodeKind, symbol_node_id
from repoheal.intelligence.calls import (
    GlobalSymbolIndex,
    build_scopes,
    resolve_dotted,
)
from repoheal.ingestion import IngestionService
from repoheal.intelligence import TreeSitterParser

pytest.importorskip("tree_sitter_languages")


def _func_id(qname: str) -> str:
    return symbol_node_id(qname, kind=NodeKind.FUNCTION)


def _method_id(qname: str) -> str:
    return symbol_node_id(qname, kind=NodeKind.METHOD)


def _class_id(qname: str) -> str:
    return symbol_node_id(qname, kind=NodeKind.CLASS)


# --- parser-level extraction ---------------------------------------------


def test_parser_emits_unresolved_calls(medium_repo: Path) -> None:
    repo = IngestionService().ingest(medium_repo)
    parser = TreeSitterParser()
    api = next(f for f in repo.files if f.path.as_posix() == "pkg/api.py")
    parsed = parser.parse_path(repo.root, api)
    callees = {c.callee_text for c in parsed.calls}
    # public_endpoint calls make_admin and handle_request.
    assert "make_admin" in callees
    assert "handle_request" in callees


def test_parser_emits_unresolved_inheritance(medium_repo: Path) -> None:
    repo = IngestionService().ingest(medium_repo)
    parser = TreeSitterParser()
    models = next(f for f in repo.files if f.path.as_posix() == "pkg/models.py")
    parsed = parser.parse_path(repo.root, models)
    bases = {(i.child_qname, i.parent_text) for i in parsed.inherits}
    assert ("pkg.models.Admin", "User") in bases


def test_parser_emits_decorator_references(medium_repo: Path) -> None:
    repo = IngestionService().ingest(medium_repo)
    parser = TreeSitterParser()
    api = next(f for f in repo.files if f.path.as_posix() == "pkg/api.py")
    parsed = parser.parse_path(repo.root, api)
    refs = {(r.referrer_qname, r.target_text) for r in parsed.references}
    assert ("pkg.api.public_endpoint", "registered") in refs
    assert ("pkg.api.legacy_endpoint", "deprecated") in refs


# --- resolver -------------------------------------------------------------


def test_resolve_dotted_finds_imported_function(medium_repo: Path) -> None:
    repo = IngestionService().ingest(medium_repo)
    parser = TreeSitterParser()
    parsed = [parser.parse_path(repo.root, f) for f in repo.files if f.path.suffix == ".py"]
    index = GlobalSymbolIndex(parsed)
    scopes = build_scopes(parsed, repo)
    api_scope = scopes["pkg.api"]

    # ``handle_request`` was imported with ``from pkg.services import handle_request``.
    # Resolution should land at pkg.services.handle_request.
    assert resolve_dotted("handle_request", api_scope, index) == "pkg.services.handle_request"
    assert resolve_dotted("make_admin", api_scope, index) == "pkg.services.make_admin"


def test_resolve_dotted_returns_none_for_unknown(medium_repo: Path) -> None:
    repo = IngestionService().ingest(medium_repo)
    parser = TreeSitterParser()
    parsed = [parser.parse_path(repo.root, f) for f in repo.files if f.path.suffix == ".py"]
    index = GlobalSymbolIndex(parsed)
    scopes = build_scopes(parsed, repo)
    services_scope = scopes["pkg.services"]

    # ``does_not_exist`` is not anywhere.
    assert resolve_dotted("does_not_exist", services_scope, index) is None


# --- end-to-end graph -----------------------------------------------------


def test_graph_has_calls_edges_across_modules(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    g = result.graph

    public = _func_id("pkg.api.public_endpoint")
    handle = _func_id("pkg.services.handle_request")
    make = _func_id("pkg.services.make_admin")

    assert g.has_node(public)
    assert g.has_node(handle)
    assert g.has_node(make)

    callees_of_public = set(g.neighbors(public, kind=EdgeKind.CALLS.value))
    assert handle in callees_of_public
    assert make in callees_of_public


def test_graph_has_inheritance_edge(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    g = result.graph
    admin = _class_id("pkg.models.Admin")
    user = _class_id("pkg.models.User")
    assert g.has_node(admin)
    assert g.has_node(user)
    assert user in set(g.neighbors(admin, kind=EdgeKind.INHERITS.value))


def test_graph_has_decorator_references(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    g = result.graph

    public = _func_id("pkg.api.public_endpoint")
    legacy = _func_id("pkg.api.legacy_endpoint")
    registered = _func_id("pkg.utils.registered")
    deprecated = _func_id("pkg.utils.deprecated")

    assert g.has_node(public) and g.has_node(legacy)
    refs_from_public = set(g.neighbors(public, kind=EdgeKind.REFERENCES.value))
    refs_from_legacy = set(g.neighbors(legacy, kind=EdgeKind.REFERENCES.value))
    assert registered in refs_from_public
    assert deprecated in refs_from_legacy


def test_phase1_circular_import_still_works(tiny_repo: Path) -> None:
    """Sanity check: Phase 2 changes did not break the Phase 1 circular-import detection."""
    result = AnalysisService().analyze(str(tiny_repo))
    matching = [f for f in result.findings if f.rule_id == "circular_imports"]
    assert matching, "Phase 2 must not regress Phase 1 cycle detection"
