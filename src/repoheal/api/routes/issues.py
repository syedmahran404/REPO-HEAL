"""Issue correlation endpoint.

Takes an issue body (and optional comments) plus a repo path; returns
correlated tracebacks and candidate files. Useful for "given this
GitHub issue body, what code do I look at?" workflows without needing
the system to fetch from GitHub itself."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...analysis import AnalysisService
from ...core.models import Issue
from ...exceptions import IngestionError, RepoHealError
from ...issues import IssueCorrelator

router = APIRouter(prefix="/issues", tags=["issues"])


class _IssueInput(BaseModel):
    title: str = ""
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)
    number: int = 0
    url: str | None = None


class CorrelateRequest(BaseModel):
    path: str
    issue: _IssueInput
    build_retrieval: bool = True


@router.post("/correlate")
def correlate(req: CorrelateRequest, request: Request) -> dict[str, Any]:
    service: AnalysisService = request.app.state.analysis
    try:
        analysis = service.analyze(req.path, build_retrieval=req.build_retrieval)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RepoHealError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    issue = Issue(
        source="api",
        number=req.issue.number,
        title=req.issue.title,
        body=req.issue.body,
        labels=tuple(req.issue.labels),
        url=req.issue.url,
        metadata={"comments": [{"body": c} for c in req.issue.comments]},
    )

    correlator = IssueCorrelator(
        analysis.repository,
        analysis.graph,
        retrieval=analysis.retrieval,
    )
    result = correlator.correlate(issue)

    return {
        "primary_anchor_node": result.primary_anchor_node,
        "candidate_files": [str(p) for p in result.candidate_files],
        "tracebacks": [
            {
                "exception_type": tb.traceback.exception_type,
                "exception_message": tb.traceback.exception_message,
                "frames": [
                    {
                        "file": str(f.frame.file),
                        "line": f.frame.line,
                        "function": f.frame.function,
                        "qualified_name": f.qualified_name,
                        "node_id": f.node_id,
                        "confidence": f.confidence,
                        "file_in_repo": str(f.file_in_repo) if f.file_in_repo else None,
                    }
                    for f in tb.frames
                ],
            }
            for tb in result.correlated_tracebacks
        ],
    }
