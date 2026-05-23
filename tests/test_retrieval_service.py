"""End-to-end retrieval service tests.

These run the full pipeline against the medium_repo fixture, which
requires tree-sitter. The pipeline uses the deterministic
HashEmbeddingProvider so tests are fully reproducible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repoheal.analysis import AnalysisService

pytest.importorskip("tree_sitter_languages")


def _service_for(path: Path):
    """Build an indexed RetrievalService for the given repo path."""
    result = AnalysisService().analyze(str(path), build_retrieval=True)
    assert result.retrieval is not None
    return result.retrieval, result


def test_retrieval_indexes_chunks_for_each_function(medium_repo: Path) -> None:
    retr, _ = _service_for(medium_repo)
    # Eight Python files, multiple functions and methods → expect at
    # least one chunk per top-level callable plus a few preambles.
    assert retr.chunk_count >= 10


def test_retrieval_finds_function_by_name(medium_repo: Path) -> None:
    retr, _ = _service_for(medium_repo)
    res = retr.search("handle_request", top_k=3)
    qnames = [c.symbol_qname for c in res.chunks if c.symbol_qname]
    assert "pkg.services.handle_request" in qnames


def test_retrieval_finds_class_by_natural_query(medium_repo: Path) -> None:
    retr, _ = _service_for(medium_repo)
    # Try a natural-language style query that should land on User/Admin.
    res = retr.search("admin user model", top_k=5)
    paths = {str(c.file_path) for c in res.chunks}
    assert any("models.py" in p for p in paths)


def test_retrieval_token_budget_truncates(medium_repo: Path) -> None:
    retr, _ = _service_for(medium_repo)
    # A tiny budget should drop most chunks.
    res = retr.search("handle_request", top_k=10, token_budget=20)
    assert len(res.chunks) <= 2


def test_retrieval_uses_cache_for_repeated_query(medium_repo: Path) -> None:
    retr, _ = _service_for(medium_repo)
    first = retr.search("handle_request", top_k=3)
    second = retr.search("handle_request", top_k=3)
    assert first.cache_hit is False
    assert second.cache_hit is True
    # Returned chunks should be identical.
    assert [c.id for c in first.chunks] == [c.id for c in second.chunks]


def test_retrieval_records_per_stage_timings(medium_repo: Path) -> None:
    retr, _ = _service_for(medium_repo)
    res = retr.search("admin", top_k=3)
    t = res.timings
    # Every stage should record a non-negative time.
    assert t.bm25_ms >= 0.0
    assert t.vector_ms >= 0.0
    assert t.fusion_ms >= 0.0
    assert t.total_ms > 0.0


def test_retrieval_search_before_index_raises(medium_repo: Path) -> None:
    from repoheal.retrieval import RetrievalService

    s = RetrievalService()
    with pytest.raises(RuntimeError):
        s.search("anything")


def test_retrieval_graph_expansion_pulls_callees(medium_repo: Path) -> None:
    """Querying for the caller should also surface callees via graph expansion."""
    retr, _ = _service_for(medium_repo)
    # public_endpoint is the entry point; its callees are make_admin & handle_request.
    res = retr.search("public_endpoint", top_k=10)
    qnames = {c.symbol_qname for c in res.chunks if c.symbol_qname}
    # Caller itself should be present.
    assert "pkg.api.public_endpoint" in qnames
    # At least one callee should also show up via expansion.
    expected_callees = {"pkg.services.handle_request", "pkg.services.make_admin"}
    assert qnames & expected_callees, f"expected one of {expected_callees} via graph expansion, got {qnames}"
