"""Tests for the Phase 2 API endpoints.

Each endpoint runs against the medium_repo fixture via FastAPI's
TestClient. We don't mock the analysis service — these tests are
end-to-end against real graph + real retrieval."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from repoheal.api.main import create_app

pytest.importorskip("tree_sitter_languages")


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# --- /retrieval/search --------------------------------------------------


def test_retrieval_search_returns_chunks(client: TestClient, medium_repo: Path) -> None:
    res = client.post(
        "/retrieval/search",
        json={"path": str(medium_repo), "query": "handle_request", "top_k": 5},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "chunks" in body
    assert "timings" in body
    assert isinstance(body["chunks"], list)
    qnames = [c.get("symbol_qname") for c in body["chunks"]]
    assert "pkg.services.handle_request" in qnames


def test_retrieval_search_validates_top_k(client: TestClient, medium_repo: Path) -> None:
    res = client.post(
        "/retrieval/search",
        json={"path": str(medium_repo), "query": "x", "top_k": 0},
    )
    assert res.status_code == 422


def test_retrieval_search_invalid_path(client: TestClient) -> None:
    res = client.post(
        "/retrieval/search",
        json={"path": "/tmp/definitely-not-a-repo-12345", "query": "x"},
    )
    assert res.status_code == 400


# --- /agents/run --------------------------------------------------------


def test_agents_list(client: TestClient) -> None:
    res = client.get("/agents/")
    assert res.status_code == 200
    assert "root_cause" in res.json()["agents"]


def test_agents_run_root_cause(client: TestClient, medium_repo: Path) -> None:
    body = {
        "path": str(medium_repo),
        "agent": "root_cause",
        "inputs": {
            "anchor_node": "function::pkg.services.handle_request",
            "max_depth": 3,
            "top_k": 5,
        },
    }
    res = client.post("/agents/run", json=body)
    assert res.status_code == 200, res.text
    result = res.json()["result"]
    assert result["status"] == "ok"
    qnames = [h["qualified_name"] for h in result["output"]["hypotheses"]]
    assert "pkg.api.public_endpoint" in qnames


def test_agents_run_unknown_agent(client: TestClient, medium_repo: Path) -> None:
    res = client.post(
        "/agents/run",
        json={"path": str(medium_repo), "agent": "does_not_exist", "inputs": {}},
    )
    assert res.status_code == 404


# --- /patches/rank ------------------------------------------------------


def test_patches_rank_orders_pass_above_fail(client: TestClient, medium_repo: Path) -> None:
    pkg_a = (medium_repo / "pkg" / "models.py").read_text(encoding="utf-8")
    new_content = pkg_a + "\n# trailing comment\n"

    body = {
        "path": str(medium_repo),
        "candidates": [
            {
                "patch": {
                    "title": "fail",
                    "edits": [{"file": "pkg/models.py", "new_content": new_content}],
                },
                "validation": {"overall": "fail"},
            },
            {
                "patch": {
                    "title": "pass",
                    "edits": [{"file": "pkg/models.py", "new_content": new_content}],
                },
                "validation": {"overall": "pass"},
            },
        ],
    }
    res = client.post("/patches/rank", json=body)
    assert res.status_code == 200, res.text
    ranked = res.json()["ranked"]
    assert ranked[0]["patch_title"] == "pass"
    assert ranked[0]["composite"] > ranked[1]["composite"]
    assert "validation" in ranked[0]["breakdown"]


def test_patches_rank_empty_candidates(client: TestClient, medium_repo: Path) -> None:
    res = client.post(
        "/patches/rank",
        json={"path": str(medium_repo), "candidates": []},
    )
    assert res.status_code == 200
    assert res.json()["ranked"] == []


# --- /issues/correlate --------------------------------------------------


def test_issues_correlate_extracts_traceback_anchor(
    client: TestClient, medium_repo: Path
) -> None:
    body = {
        "path": str(medium_repo),
        "issue": {
            "title": "Bug",
            "body": (
                'Traceback (most recent call last):\n'
                '  File "pkg/services.py", line 16, in handle_request\n'
                '    return user.name\n'
                "AttributeError: 'NoneType' object has no attribute 'name'\n"
            ),
            "labels": ["bug"],
        },
    }
    res = client.post("/issues/correlate", json=body)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["primary_anchor_node"] is not None
    assert "pkg/services.py" in [str(p) for p in payload["candidate_files"]]
    assert payload["tracebacks"]
    assert payload["tracebacks"][0]["exception_type"] == "AttributeError"


def test_issues_correlate_without_traceback_uses_retrieval(
    client: TestClient, medium_repo: Path
) -> None:
    body = {
        "path": str(medium_repo),
        "issue": {
            "title": "handle_request fails on None user",
            "body": "When the user is None the code crashes.",
        },
    }
    res = client.post("/issues/correlate", json=body)
    assert res.status_code == 200
    payload = res.json()
    assert payload["primary_anchor_node"] is None  # no traceback
    assert payload["candidate_files"]  # retrieval supplied them


# --- /telemetry/spans/recent --------------------------------------------


def test_telemetry_endpoint(client: TestClient) -> None:
    res = client.get("/telemetry/spans/recent")
    assert res.status_code == 200
    body = res.json()
    assert "enabled" in body
    assert "spans" in body
