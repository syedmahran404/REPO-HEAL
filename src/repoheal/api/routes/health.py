"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from ... import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "version": __version__}


@router.get("/ready")
def ready() -> dict[str, str]:
    """Readiness probe.

    Phase 1 has no external dependencies that can be unready; this
    always returns ok. The signature is kept stable for when we add
    DB / queue / sandbox readiness."""
    return {"status": "ok"}
