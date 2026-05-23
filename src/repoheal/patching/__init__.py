"""Patching subsystem.

* :class:`UnifiedDiffGenerator` — turn a :class:`Patch` into a unified
  diff string suitable for ``git apply`` or human review.
* :class:`PatchApplier` — apply a Patch to a repository's working tree
  with snapshot-based rollback.
* :class:`PatchRanker` (Phase 2) — score and order candidate patches
  using composable scorers (validation, diff size, graph impact,
  style consistency).

This subsystem is responsible for the *mechanics* of applying and
choosing between fixes. The decision of *what* to fix lives in the
agents (Phase 6) and the detection rules (Phase 7).
"""

from .applier import PatchApplier
from .diff import UnifiedDiffGenerator
from .ranking import (
    DiffSizeScorer,
    GraphImpactScorer,
    PatchCandidate,
    PatchRanker,
    RankingContext,
    RankingScore,
    Scorer,
    StyleConsistencyScorer,
    ValidationScorer,
    default_scorers,
)

__all__ = [
    "DiffSizeScorer",
    "GraphImpactScorer",
    "PatchApplier",
    "PatchCandidate",
    "PatchRanker",
    "RankingContext",
    "RankingScore",
    "Scorer",
    "StyleConsistencyScorer",
    "UnifiedDiffGenerator",
    "ValidationScorer",
    "default_scorers",
]
