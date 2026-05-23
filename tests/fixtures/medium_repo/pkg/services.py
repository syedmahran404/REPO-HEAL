"""Service layer.

Deliberate Phase 2 markers:
- ``handle_request`` is called from ``api.public_endpoint`` → CALLS edge.
- ``long_method`` exceeds the line threshold → ``long_method`` rule positive.
- ``_helper_unused`` is private and never called → ``dead_code`` rule positive.
"""

from __future__ import annotations

from pkg.models import Admin, User


def handle_request(user: User) -> str:
    """Handle a request. Used by the API layer."""
    return user.name


def long_method(x: int) -> int:
    """Bug fixture: too many lines."""
    a = 1
    b = 2
    c = 3
    d = 4
    e = 5
    f = 6
    g = 7
    h = 8
    i = 9
    j = 10
    k = 11
    l = 12  # noqa: E741
    m = 13
    n = 14
    o = 15  # noqa: E741
    p = 16
    q = 17
    r = 18
    s = 19
    t = 20
    u = 21
    v = 22
    w = 23
    return a + b + c + d + e + f + g + h + i + j + k + l + m + n + o + p + q + r + s + t + u + v + w + x


def make_admin(name: str) -> Admin:
    """Used by the API layer; keeps ``Admin`` referenced as a callable."""
    return Admin(name)


def _helper_unused() -> None:
    """Bug fixture: dead code (private, no callers)."""
    return None
