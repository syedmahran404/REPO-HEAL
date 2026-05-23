"""API surface.

Deliberate Phase 2 markers:
- Imports cross modules → IMPORTS edges.
- Calls ``handle_request`` and ``make_admin`` → CALLS edges.
- Uses ``@registered`` and ``@deprecated`` → REFERENCES edges.
"""

from __future__ import annotations

from pkg.services import handle_request, make_admin
from pkg.utils import deprecated, registered


@registered
def public_endpoint(name: str) -> str:
    """Public route — registered via decorator."""
    user = make_admin(name)
    return handle_request(user)


@deprecated
def legacy_endpoint() -> None:
    """Old route — deprecated."""
    return None
