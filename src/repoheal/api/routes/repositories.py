"""Repository ingestion + analysis endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...analysis import AnalysisService
from ...exceptions import IngestionError

router = APIRouter(prefix="/repositories", tags=["repositories"])


class IngestRequest(BaseModel):
    """POST /repositories/ingest body."""

    path: str | None = Field(default=None, description="Local path on the server.")
    url: str | None = Field(default=None, description="Git URL to clone.")
    branch: str | None = None
    rule_ids: list[str] | None = Field(
        default=None,
        description="If set, only run these detection rules. Defaults to all.",
    )

    def source(self) -> str:
        if self.path:
            return self.path
        if self.url:
            return self.url
        raise ValueError("either path or url must be provided")


@router.post("/ingest")
def ingest(req: IngestRequest, request: Request) -> dict[str, Any]:
    """Ingest, parse, build graph, scan. Synchronous in Phase 1."""
    if not req.path and not req.url:
        raise HTTPException(status_code=422, detail="path or url is required")

    service: AnalysisService = request.app.state.analysis
    try:
        result = service.analyze(req.source(), branch=req.branch, rule_ids=req.rule_ids)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "repository": _serialize_repo(result.repository),
        "parsed_file_count": len(result.parsed_files),
        "graph": result.graph_stats(),
        "findings": [_serialize_finding(f) for f in result.findings],
    }


def _serialize_repo(repo: Any) -> dict[str, Any]:
    return {
        "id": str(repo.id),
        "name": repo.name,
        "root": str(repo.root),
        "origin": repo.origin,
        "branch": repo.branch,
        "commit": repo.commit,
        "ecosystem": {
            "languages": [l.value for l in repo.ecosystem.languages],
            "build_systems": [b.value for b in repo.ecosystem.build_systems],
            "frameworks": list(repo.ecosystem.frameworks),
            "test_frameworks": list(repo.ecosystem.test_frameworks),
            "package_managers": list(repo.ecosystem.package_managers),
            "has_dockerfile": repo.ecosystem.has_dockerfile,
            "has_ci": repo.ecosystem.has_ci,
        },
        "file_count": repo.file_count,
    }


def _serialize_finding(f: Any) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "rule_id": f.rule_id,
        "title": f.title,
        "description": f.description,
        "severity": f.severity.value,
        "file": str(f.file) if isinstance(f.file, Path) else f.file,
        "related_files": [str(p) for p in f.related_files],
        "metadata": f.metadata,
    }
