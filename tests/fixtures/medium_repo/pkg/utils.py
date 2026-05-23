"""Decorators and helpers.

Deliberate Phase 2 markers in this file:
- An unused stdlib import (``os``).
- A function with a mutable default argument.
- A function with a broad ``except Exception`` and ``pass``.
- Two decorators that other modules use, so they pick up REFERENCES edges.
"""

from __future__ import annotations

import os  # repoheal:unused — should trip ``unused_imports``.
from typing import Callable


def registered(func: Callable) -> Callable:
    """Decorator: marks a function as registered (used in api.py)."""
    return func


def deprecated(func: Callable) -> Callable:
    """Decorator: marks a function as deprecated."""
    return func


def with_mutable_default(items: list = []) -> list:  # noqa: B006 - intentional bug
    """Bug fixture: mutable default argument."""
    items.append("x")
    return items


def broad_handler() -> None:
    """Bug fixture: broad except + bare pass."""
    try:
        do_thing()
    except Exception:  # noqa: BLE001 - intentional
        pass


def do_thing() -> None:
    """Helper called by ``broad_handler``."""
    return None
