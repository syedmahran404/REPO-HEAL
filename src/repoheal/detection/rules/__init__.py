"""Detection rules.

# Roadmap of rules to add (one PR each):

* `unused_imports`            — module imports nothing actually references.
* `mutable_default_args`      — Python `def f(x=[])`.
* `broad_except`              — bare `except:` or `except Exception:` w/o re-raise.
* `async_deadlock`            — `await` inside a sync context manager that holds a Lock.
* `missing_test_coverage`     — graph node has no test referencing it.
* `god_class`                 — class with >25 methods or >500 LOC.
* `long_method`               — function body >100 lines.
* `dead_code`                 — function with zero in-edges (CALLS or REFERENCES).
* `n_plus_one_query`          — ORM iteration with per-iteration query call.
* `hardcoded_secret`          — high-entropy string literal that looks like a key.

Each will live in a per-language file in this directory.
"""

from .python_rules import CircularImportRule

__all__ = ["CircularImportRule"]
