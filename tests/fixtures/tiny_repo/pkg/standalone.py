"""A module deliberately *not* in any cycle."""

from __future__ import annotations

import json  # stdlib import: should resolve to None (external)


def parse(s: str) -> object:
    return json.loads(s)
