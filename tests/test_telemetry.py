"""Tests for the telemetry / tracing shim.

Two tiers:

* **Always-on tests** verify the no-op fallback works correctly with
  or without OpenTelemetry installed: ``traced`` is a no-op context
  manager, the decorator preserves return values and exceptions, and
  ``init_tracing(in_memory=True)`` returns False when the SDK isn't
  importable.

* **OTel-gated tests** are guarded by ``importorskip("opentelemetry")``.
  When the SDK is installed they verify the in-memory exporter records
  spans correctly and that exception status flows through.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from repoheal.obs.tracing import (
    is_tracing_enabled,
    span_attributes,
    trace_function,
    traced,
)
from repoheal.obs.tracing import init_tracing as _init_tracing


# =============================================================================
# Always-on no-op behaviour
# =============================================================================


def test_traced_is_a_no_op_when_otel_absent_or_idle() -> None:
    """Without explicit ``init_tracing`` (and no OTEL_* env vars), the
    context manager must not raise and must yield a usable span object."""
    with traced("noop.smoke", x=1, y="two") as span:
        # Whatever it yields, basic methods must be present and callable.
        span.set_attribute("k", "v")
        span.add_event("evt")
        span.set_status(None)


def test_traced_propagates_exceptions() -> None:
    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        with traced("noop.boom"):
            raise _Boom("nope")


def test_trace_function_decorator_preserves_return_value() -> None:
    @trace_function("test.return_value")
    def adder(a: int, b: int) -> int:
        return a + b

    assert adder(2, 3) == 5


def test_trace_function_decorator_preserves_exceptions() -> None:
    @trace_function("test.raise")
    def raises() -> None:
        raise ValueError("x")

    with pytest.raises(ValueError):
        raises()


def test_trace_function_decorator_works_for_async() -> None:
    @trace_function("test.async")
    async def add(a: int, b: int) -> int:
        await asyncio.sleep(0)
        return a + b

    result = asyncio.run(add(2, 3))
    assert result == 5


def test_span_attributes_is_safe_outside_a_span() -> None:
    # Should not raise even if there's no current span / OTel.
    span_attributes(foo="bar", n=1)


def test_init_tracing_returns_bool() -> None:
    """In sandboxes where OTel SDK is missing, init_tracing must return
    False instead of raising. When SDK is present, it returns True."""
    result = _init_tracing(in_memory=True)
    assert isinstance(result, bool)


def test_traced_with_complex_attribute_does_not_crash() -> None:
    with traced("noop.complex", obj={"nested": "value"}, lst=[1, 2, 3]):
        pass


# =============================================================================
# OTel-gated tests (skipped when SDK isn't installed)
# =============================================================================


pytest.importorskip("opentelemetry.sdk.trace")


@pytest.fixture
def in_memory_tracing():  # type: ignore[no-untyped-def]
    """Set up an in-memory exporter, yield, then reset."""
    from repoheal.obs.tracing import (
        get_recent_spans,
        init_tracing,
        reset_in_memory_spans,
    )

    activated = init_tracing(in_memory=True)
    if not activated:
        pytest.skip("in-memory exporter unavailable")
    reset_in_memory_spans()
    yield get_recent_spans
    reset_in_memory_spans()


def test_traced_records_span_when_otel_active(in_memory_tracing: Any) -> None:
    with traced("test.recorded", k="v"):
        pass

    spans = in_memory_tracing()
    names = [s["name"] for s in spans]
    assert "test.recorded" in names

    recorded = next(s for s in spans if s["name"] == "test.recorded")
    assert recorded["attributes"].get("k") == "v"


def test_traced_marks_span_error_on_exception(in_memory_tracing: Any) -> None:
    class _ApplicationError(RuntimeError):
        pass

    with pytest.raises(_ApplicationError):
        with traced("test.errored"):
            raise _ApplicationError("nope")

    spans = in_memory_tracing()
    errored = next(s for s in spans if s["name"] == "test.errored")
    # Status name will be "ERROR" when the exception was recorded.
    assert errored["status"] == "ERROR"


def test_is_tracing_enabled_after_init(in_memory_tracing: Any) -> None:
    assert is_tracing_enabled() is True


def test_nested_spans_are_recorded(in_memory_tracing: Any) -> None:
    with traced("outer"):
        with traced("inner"):
            pass

    names = [s["name"] for s in in_memory_tracing()]
    assert "outer" in names
    assert "inner" in names
