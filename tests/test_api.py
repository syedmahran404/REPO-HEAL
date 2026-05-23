"""End-to-end API tests using FastAPI's TestClient."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from repoheal.api.main import create_app

pytest.importorskip("tree_sitter_languages")


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_ready(client: TestClient) -> None:
    res = client.get("/ready")
    assert res.status_code == 200


def test_ingest_local_path(client: TestClient, tiny_repo: Path) -> None:
    res = client.post(
        "/repositories/ingest",
        json={"path": str(tiny_repo)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["repository"]["name"] == "tiny_repo"
    assert "python" in body["repository"]["ecosystem"]["languages"]
    assert body["graph"]["nodes"] > 0
    assert any(f["rule_id"] == "circular_imports" for f in body["findings"])


def test_ingest_requires_path_or_url(client: TestClient) -> None:
    res = client.post("/repositories/ingest", json={})
    assert res.status_code == 422


def test_graph_cycles(client: TestClient, tiny_repo: Path) -> None:
    res = client.get("/graph/cycles", params={"path": str(tiny_repo), "kind": "imports"})
    assert res.status_code == 200
    body = res.json()
    assert body["count"] >= 1
