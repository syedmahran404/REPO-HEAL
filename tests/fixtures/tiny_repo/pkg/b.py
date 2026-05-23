"""Module B — imports A; the other side of the deliberate cycle."""

from __future__ import annotations

from pkg import a  # noqa: F401 - intentional: forms a cycle with pkg.a


def function_in_b() -> str:
    return "b"
