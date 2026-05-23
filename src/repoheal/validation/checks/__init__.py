"""Concrete validators.

Phase 1: ``SyntaxValidator`` (Python, real).

Planned (each one PR):
  * ``LintValidator``        — wraps `ruff check`.
  * ``TypeCheckValidator``   — wraps `mypy`.
  * ``UnitTestValidator``    — wraps `pytest --co -q` then `pytest`.
  * ``BuildValidator``       — wraps the ecosystem's build command.
  * ``SecurityScanValidator``— wraps `detect-secrets`.

All run inside the sandbox runner.
"""
