"""Detection rules.

Phase 1:
* :class:`CircularImportRule` — module-level import cycles via SCC.

Phase 2:
* :class:`UnusedImportRule`        — names imported but never used.
* :class:`MutableDefaultArgsRule`  — ``def f(x=[])`` and friends.
* :class:`BroadExceptRule`         — ``except Exception:`` / bare except w/o re-raise.
* :class:`DeadCodeRule`            — private functions/methods with zero in-edges.
* :class:`LongMethodRule`          — body exceeds the configured line threshold.
* :class:`GodClassRule`            — class with too many methods.
* :class:`HardcodedSecretRule`     — high-entropy / known-pattern secrets in source.

Roadmap (one PR each):
* `async_deadlock`            — `await` inside a sync context manager that holds a Lock.
* `n_plus_one_query`          — ORM iteration with per-iteration query call.
* `race_condition`            — concurrency hazard heuristic.
* `missing_test_coverage`     — graph node has no test referencing it.
"""

from .python_rules import (
    BroadExceptRule,
    CircularImportRule,
    DeadCodeRule,
    GodClassRule,
    HardcodedSecretRule,
    LongMethodRule,
    MutableDefaultArgsRule,
    UnusedImportRule,
)

__all__ = [
    "BroadExceptRule",
    "CircularImportRule",
    "DeadCodeRule",
    "GodClassRule",
    "HardcodedSecretRule",
    "LongMethodRule",
    "MutableDefaultArgsRule",
    "UnusedImportRule",
]
