"""Agent invocation endpoint.

Runs a single agent (looked up in the default registry) against the
graph + repo + retrieval state derived from a path. This is *not* the
orchestrator — for Phase 2 a single-step path is the most useful API
surface; multi-step DAG execution lives behind a future
``/orchestrator/run`` endpoint.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...agents import AgentRunner, default_registry
from ...analysis import AnalysisService
from ...exceptions import IngestionError, RepoHealError

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentRunRequest(BaseModel):
    path: str = Field(..., description="Local path to the repository.")
    agent: str = Field(..., description="Registered agent name (e.g. 'root_cause').")
    inputs: dict[str, Any] = Field(default_factory=dict)
    build_retrieval: bool = True
    max_attempts: int = Field(default=2, ge=1, le=10)
    timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)


@router.post("/run")
async def run_agent(req: AgentRunRequest, request: Request) -> dict[str, Any]:
    service: AnalysisService = request.app.state.analysis

    try:
        result = service.analyze(req.path, build_retrieval=req.build_retrieval)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RepoHealError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    registry = default_registry()
    if not registry.has(req.agent):
        raise HTTPException(
            status_code=404,
            detail=f"unknown agent: {req.agent!r} (registered: {sorted(registry.names())})",
        )

    agent = registry.create(req.agent)
    runner = AgentRunner(
        agent,
        max_attempts=req.max_attempts,
        timeout_seconds=req.timeout_seconds,
    )
    state = {
        "repo": result.repository,
        "graph": result.graph,
        "retrieval": result.retrieval,
        "inputs": req.inputs,
    }
    agent_result = await runner.run(state)

    return {"result": agent_result.model_dump(mode="json")}


@router.get("/")
def list_agents() -> dict[str, list[str]]:
    """List the names of every registered agent."""
    return {"agents": sorted(default_registry().names())}
