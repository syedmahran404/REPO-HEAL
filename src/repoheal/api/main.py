"""FastAPI application factory.

Two-stage construction:

1. ``create_app()`` builds an ``AnalysisService`` and wires routes.
2. The module-level ``app`` is created via ``create_app()`` so
   ``uvicorn repoheal.api.main:app`` works out of the box.

Tests should call ``create_app()`` with their own service injected.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from ..analysis import AnalysisService
from ..config import get_settings
from ..logging import configure_logging, get_logger
from .routes import agents as agent_routes
from .routes import graph as graph_routes
from .routes import health as health_routes
from .routes import issues as issue_routes
from .routes import patches as patch_routes
from .routes import repositories as repo_routes
from .routes import retrieval as retrieval_routes
from .routes import telemetry as telemetry_routes


def create_app(*, analysis: AnalysisService | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("repoheal.api")

    service = analysis or AnalysisService()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("api.startup", workdir=str(settings.workdir))
        settings.ensure_workdir()
        yield
        log.info("api.shutdown")

    app = FastAPI(
        title="REPO-HEAL",
        description="Autonomous repository analysis & self-healing engine.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Make the service available to routers via ``app.state``.
    app.state.analysis = service

    app.include_router(health_routes.router)
    app.include_router(repo_routes.router)
    app.include_router(graph_routes.router)
    app.include_router(retrieval_routes.router)
    app.include_router(agent_routes.router)
    app.include_router(patch_routes.router)
    app.include_router(issue_routes.router)
    app.include_router(telemetry_routes.router)

    return app


# Module-level instance for ``uvicorn ... :app``
app = create_app()
