"""Hybrid retrieval endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...analysis import AnalysisService
from ...exceptions import IngestionError, RepoHealError

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


class SearchRequest(BaseModel):
    path: str = Field(..., description="Local path to the repository.")
    query: str = Field(..., min_length=1, description="Free-form search query.")
    top_k: int = Field(default=10, ge=1, le=100)
    token_budget: int | None = Field(default=None, ge=1)
    graph_expand: bool = True


@router.post("/search")
def search(req: SearchRequest, request: Request) -> dict[str, Any]:
    """Build a retrieval index over the repo at ``path`` and search it.

    For Phase 2 the index is built per request — fine for ad-hoc usage,
    not what we want for a hot service. Phase 3 adds session caching."""
    service: AnalysisService = request.app.state.analysis
    try:
        result = service.analyze(req.path, build_retrieval=True)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RepoHealError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if result.retrieval is None:
        raise HTTPException(status_code=500, detail="retrieval index unavailable")

    res = result.retrieval.search(
        req.query,
        top_k=req.top_k,
        token_budget=req.token_budget,
        graph_expand=req.graph_expand,
    )

    return {
        "chunks": [_serialize_chunk(c) for c in res.chunks],
        "timings": {
            "bm25_ms": round(res.timings.bm25_ms, 2),
            "vector_ms": round(res.timings.vector_ms, 2),
            "fusion_ms": round(res.timings.fusion_ms, 2),
            "expansion_ms": round(res.timings.expansion_ms, 2),
            "packing_ms": round(res.timings.packing_ms, 2),
            "total_ms": round(res.timings.total_ms, 2),
        },
        "cache_hit": res.cache_hit,
        "chunk_count": result.retrieval.chunk_count,
    }


def _serialize_chunk(chunk: Any) -> dict[str, Any]:
    return {
        "id": chunk.id,
        "file_path": str(chunk.file_path) if isinstance(chunk.file_path, Path) else chunk.file_path,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "symbol_qname": chunk.symbol_qname,
        "language": chunk.language.value if hasattr(chunk.language, "value") else str(chunk.language),
        "text": chunk.text,
    }
