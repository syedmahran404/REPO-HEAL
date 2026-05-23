"""Concrete validators.

Phase 1: ``SyntaxValidator`` (Python, AST-based).

Phase 2 adds three sandbox-backed validators:

* ``RuffLintValidator``  — wraps ``ruff check`` with JSON output.
* ``MypyTypeValidator``  — wraps ``mypy`` with structured parse.
* ``PytestValidator``    — wraps ``pytest`` and parses summary + failures.

All three accept an injectable :class:`SandboxRunner` so tests can
substitute a fake runner with canned outputs.
"""

from .lint import RuffLintValidator
from .syntax import SyntaxValidator
from .tests import PytestValidator
from .types import MypyTypeValidator

__all__ = [
    "MypyTypeValidator",
    "PytestValidator",
    "RuffLintValidator",
    "SyntaxValidator",
]
