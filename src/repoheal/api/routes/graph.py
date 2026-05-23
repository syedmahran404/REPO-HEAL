"""Graph inspection endpoints.

These endpoints accept a path and produce a graph summary on the fly.
A future PR will let clients persist a repo and refer to it by ID; the
route shape is designed so the handler signature stays the same.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ...analysis import AnalysisService
from ...exceptions import IngestionError
from ...graph.queries import ImpactAnalysis

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/build")
def build_graph(
    request: Request,
    path: str = Query(..., description="Local path to the repository."),
    branch: str | None = None,
) -> dict[str, Any]:
    """Build the graph for a path and return a summary + JSON dump."""
    service: AnalysisService = request.app.state.analysis
    try:
        result = service.analyze(path, branch=branch)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "stats": result.graph_stats(),
        "graph": result.graph.to_dict(),
    }


@router.get("/cycles")
def cycles(
    request: Request,
    path: str = Query(...),
    kind: str = Query("imports"),
) -> dict[str, Any]:
    """Find graph cycles among edges of the given kind."""
    service: AnalysisService = request.app.state.analysis
    try:
        result = service.analyze(path)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cycles_found = result.graph.find_cycles(kind=kind)
    return {"kind": kind, "cycles": cycles_found, "count": len(cycles_found)}


@router.get("/impact")
def impact(
    request: Request,
    path: str = Query(...),
    node: str = Query(..., description="Node id, e.g. 'module::pkg.mod'"),
    direction: str = Query("downstream", regex="^(downstream|upstream)$"),
    max_depth: int = Query(5, ge=1, le=20),
) -> dict[str, Any]:
    """Run impact analysis from a starting node."""
    service: AnalysisService = request.app.state.analysis
    try:
        result = service.analyze(path)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not result.graph.has_node(node):
        raise HTTPException(status_code=404, detail=f"node not found: {node}")

    analysis = ImpactAnalysis(result.graph)
    if direction == "downstream":
        nodes = analysis.downstream(node, max_depth=max_depth)
    else:
        nodes = analysis.upstream(node, max_depth=max_depth)

    return {
        "start": node,
        "direction": direction,
        "max_depth": max_depth,
        "nodes": sorted(nodes),
        "count": len(nodes),
    }
