"""OpenTelemetry tracing wrappers.

Design principles:

* **Optional dependency.** When ``opentelemetry-api`` is not importable,
  every helper here is a no-op. ``pip install repoheal`` users who don't
  want telemetry pay zero cost: zero deps, zero hot-path overhead beyond
  one ``contextmanager`` enter and exit.

* **Lazy tracer lookup.** We don't cache a tracer at import time because
  ``init_tracing`` may run later. Calling ``get_tracer`` on every span
  is cheap (the tracer is the SDK's own cached instance once a provider
  is set).

* **Sync + async parity.** ``traced`` is a context manager and works
  identically inside sync and async code. ``trace_function`` wraps
  either kind of callable transparently.

* **Fail-soft.** If a span creation itself raises (it shouldn't, but
  defensive), the wrapped block still runs.

Configure a real exporter via standard OTel env vars (``OTEL_*``) or
explicitly via :func:`init_tracing`. The shim's no-op fallback means
agent code can call ``traced(...)`` everywhere without checking
whether tracing is configured.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import os
from collections.abc import Iterator
from typing import Any, Callable, TypeVar

from ..logging import get_logger

_log = get_logger(__name__)

# Detect at import: is the OTel API installed?
try:  # pragma: no cover — exercised in OTel-installed environments
    from opentelemetry import trace as _otel_trace  # type: ignore[import-not-found]

    _OTEL_API = True
except ImportError:
    _otel_trace = None  # type: ignore[assignment]
    _OTEL_API = False


_SERVICE_NAME = "repoheal"
_TRACER_NAME = "repoheal"


# =============================================================================
# Public API
# =============================================================================


def is_tracing_enabled() -> bool:
    """Return True if OpenTelemetry is importable AND a TracerProvider
    has been configured (either by us via :func:`init_tracing` or by
    the host application via the standard ``OTEL_*`` env vars)."""
    if not _OTEL_API:
        return False
    provider = _otel_trace.get_tracer_provider()
    cls_name = type(provider).__name__
    # When no provider is set, OTel returns a NoOpTracerProvider.
    return cls_name not in ("ProxyTracerProvider", "NoOpTracerProvider", "DefaultTracerProvider")


def init_tracing(
    *,
    service_name: str = _SERVICE_NAME,
    exporter_endpoint: str | None = None,
    in_memory: bool = False,
) -> bool:
    """Initialise tracing. Returns True if a real provider was set.

    Parameters
    ----------
    service_name:
        Goes into the OTel ``service.name`` resource attribute.
    exporter_endpoint:
        OTLP/HTTP endpoint (e.g. ``"http://localhost:4318/v1/traces"``).
        Falls back to ``REPOHEAL_OTLP_ENDPOINT`` env var if unset.
    in_memory:
        If True, install an in-memory exporter so tests can read back
        recorded spans via :func:`get_recent_spans`. Mutually
        exclusive with ``exporter_endpoint``.

    Returns False (no-op) when ``opentelemetry-sdk`` isn't installed
    or when neither in_memory nor a working OTLP endpoint is provided.
    The caller can call ``init_tracing`` multiple times safely; only
    the first call wins (OTel rejects subsequent provider sets with a
    warning, which we suppress).
    """
    global _IN_MEMORY_EXPORTER

    if not _OTEL_API:
        return False

    try:  # pragma: no cover - environment-dependent
        from opentelemetry.sdk.resources import Resource  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace.export import (  # type: ignore[import-not-found]
            BatchSpanProcessor,
            SimpleSpanProcessor,
        )
    except ImportError:
        _log.info("tracing.sdk_unavailable")
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))

    endpoint = exporter_endpoint or os.getenv("REPOHEAL_OTLP_ENDPOINT")

    if in_memory:
        try:  # pragma: no cover
            from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # type: ignore[import-not-found]
                InMemorySpanExporter,
            )
        except ImportError:
            _log.warning("tracing.in_memory_exporter_unavailable")
            return False
        _IN_MEMORY_EXPORTER = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(_IN_MEMORY_EXPORTER))
    elif endpoint:
        try:  # pragma: no cover
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
                OTLPSpanExporter,
            )
        except ImportError:
            _log.warning("tracing.otlp_exporter_unavailable", endpoint=endpoint)
            return False
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    else:
        # No exporter chosen — useful as a smoke test that confirms the
        # SDK is wired in but produces no output.
        pass

    _otel_trace.set_tracer_provider(provider)
    _log.info(
        "tracing.initialised",
        service=service_name,
        endpoint=endpoint,
        in_memory=in_memory,
    )
    return True


@contextlib.contextmanager
def traced(name: str, **attributes: Any) -> Iterator[Any]:
    """Context manager that emits one span around the wrapped block.

    Usage:

    .. code-block:: python

        with traced("ingestion.walk", root=str(root)):
            for f in walker.walk(root):
                ...

    Attributes are coerced to OTel-safe values (str, int, float, bool).
    Exceptions inside the block are recorded on the span before being
    re-raised.
    """
    if not _OTEL_API:
        yield _NoOpSpan()
        return

    tracer = _otel_trace.get_tracer(_TRACER_NAME)
    try:
        cm = tracer.start_as_current_span(name)
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("tracing.span_create_failed", span=name, error=str(exc))
        yield _NoOpSpan()
        return

    with cm as span:
        for k, v in attributes.items():
            try:
                span.set_attribute(k, _safe_attr(v))
            except Exception:  # pragma: no cover
                pass
        try:
            yield span
        except Exception as exc:
            try:
                span.record_exception(exc)
                span.set_status(_otel_trace.Status(_otel_trace.StatusCode.ERROR, str(exc)))
            except Exception:  # pragma: no cover
                pass
            raise


def trace_function(name: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: wrap a function in a span. Works for sync and async."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        span_name = name or f"{fn.__module__}.{fn.__qualname__}"

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with traced(span_name):
                    return await fn(*args, **kwargs)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with traced(span_name):
                return fn(*args, **kwargs)

        return sync_wrapper

    return decorator


def span_attributes(**kwargs: Any) -> None:
    """Attach attributes to the *current* span (the innermost active one).

    Cheap no-op outside an active span or when OTel isn't installed."""
    if not _OTEL_API:
        return
    span = _otel_trace.get_current_span()
    # The default no-op span object accepts set_attribute calls but does
    # nothing — that's fine for us.
    for k, v in kwargs.items():
        try:
            span.set_attribute(k, _safe_attr(v))
        except Exception:  # pragma: no cover
            pass


# =============================================================================
# Internal
# =============================================================================


_IN_MEMORY_EXPORTER: Any | None = None


def get_recent_spans() -> list[dict[str, Any]]:
    """Return finished spans from the in-memory exporter, if active.

    Useful for tests and the dev-mode ``/telemetry/spans/recent`` API.
    """
    if _IN_MEMORY_EXPORTER is None:
        return []
    try:  # pragma: no cover
        spans = _IN_MEMORY_EXPORTER.get_finished_spans()
    except Exception:
        return []
    return [_span_to_dict(s) for s in spans]


def reset_in_memory_spans() -> None:
    """Clear the in-memory span buffer (test helper)."""
    if _IN_MEMORY_EXPORTER is None:
        return
    try:  # pragma: no cover
        _IN_MEMORY_EXPORTER.clear()
    except Exception:
        pass


def _span_to_dict(span: Any) -> dict[str, Any]:  # pragma: no cover - SDK shape
    return {
        "name": getattr(span, "name", "<?>"),
        "duration_ns": int(span.end_time - span.start_time)
        if getattr(span, "end_time", None) and getattr(span, "start_time", None)
        else None,
        "attributes": dict(span.attributes) if getattr(span, "attributes", None) else {},
        "status": getattr(getattr(span, "status", None), "status_code", None).name
        if getattr(span, "status", None) is not None
        else None,
    }


def _safe_attr(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)) and all(isinstance(x, (str, int, float, bool)) for x in v):
        return list(v)
    return str(v)


class _NoOpSpan:
    """Returned by ``traced`` when OTel isn't installed."""

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def record_exception(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def add_event(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        return None


F = TypeVar("F", bound=Callable[..., Any])


__all__ = [
    "get_recent_spans",
    "init_tracing",
    "is_tracing_enabled",
    "reset_in_memory_spans",
    "span_attributes",
    "trace_function",
    "traced",
]
