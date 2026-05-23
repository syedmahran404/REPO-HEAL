"""Module A — imports B; participates in the deliberate cycle."""

from __future__ import annotations

from pkg import b  # noqa: F401 - intentional: forms a cycle with pkg.b


CONSTANT_A = "a"


def function_in_a() -> str:
    """Return a fixed string."""
    return CONSTANT_A


class ClassA:
    """Top-level class for symbol-extraction tests."""

    def method_one(self) -> int:
        return 1

    def method_two(self, x: int) -> int:
        def nested(y: int) -> int:
            return x + y

        return nested(x)
