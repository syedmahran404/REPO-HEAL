"""Telemetry / observability endpoints.

Currently exposes the in-memory span buffer for development. When
``REPOHEAL_OTLP_ENDPOINT`` is set and the OTLP exporter is active,
the in-memory buffer is empty (spans go to the collector instead).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...obs.tracing import get_recent_spans, is_tracing_enabled

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/spans/recent")
def recent_spans() -> dict[str, Any]:
    return {
        "enabled": is_tracing_enabled(),
        "spans": get_recent_spans(),
    }
