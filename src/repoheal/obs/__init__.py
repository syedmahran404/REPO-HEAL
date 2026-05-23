"""Observability — distributed tracing (Phase 2), metrics (planned).

This package owns every cross-cutting "watch what's happening" concern:

* :mod:`obs.tracing` — OpenTelemetry-flavoured ``traced`` context
  manager + ``trace_function`` decorator. Falls back to a no-op when
  OpenTelemetry isn't installed.
* :mod:`obs.spans` — dev-mode introspection: dump the recent span
  buffer when an in-memory exporter is configured.

Phase 2 ships tracing only. Metrics (Prometheus / OTel meters) are the
next addition; the seam is intentionally narrow so adding them doesn't
touch hot paths.
"""

from .tracing import (
    init_tracing,
    is_tracing_enabled,
    span_attributes,
    trace_function,
    traced,
)

__all__ = [
    "init_tracing",
    "is_tracing_enabled",
    "span_attributes",
    "trace_function",
    "traced",
]
