"""Patching subsystem.

* :class:`UnifiedDiffGenerator` — turn a :class:`Patch` into a unified
  diff string suitable for ``git apply`` or human review.
* :class:`PatchApplier` — apply a Patch to a repository's working tree
  with snapshot-based rollback.

This subsystem is responsible for the *mechanics* of applying a fix.
The decision of *what* to fix lives in the agents (Phase 6) and the
detection rules (Phase 7).
"""

from .applier import PatchApplier
from .diff import UnifiedDiffGenerator

__all__ = ["PatchApplier", "UnifiedDiffGenerator"]
