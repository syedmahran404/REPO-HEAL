"""Mypy type-check validator.

Runs ``mypy --no-error-summary --no-pretty`` inside the sandbox over
the patch's Python files. Parses the ``path:line: severity: msg [code]``
line format into structured problems.

Verdict policy mirrors the lint validator:

* No Python files in patch → ``PASS``.
* Zero errors → ``PASS`` (warnings/notes still surfaced for context).
* ≥1 error → ``FAIL``.
* mypy crashed / non-parseable output → ``WARN`` with raw head.
* Tool missing → ``WARN``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ...core.models import Patch, Repository, ValidationResult, Verdict
from ...core.protocols import SandboxRunner
from ...exceptions import SandboxError, SandboxTimeoutError
from ...sandbox import SubprocessRunner
from .lint import _python_files_in_patch, _tool_missing_or_reraise

# `path:line: severity: message [code]`. The optional column makes
# the line number column conditional. We accept both forms.
_MYPY_RE = re.compile(
    r"^(?P<file>[^:]+?):(?P<line>\d+)(?::\d+)?:\s*"
    r"(?P<sev>error|warning|note):\s*"
    r"(?P<msg>.+?)"
    r"(?:\s+\[(?P<code>[^\]]+)\])?\s*$"
)


class MypyTypeValidator:
    """Run ``mypy`` and return structured results."""

    def __init__(
        self,
        *,
        sandbox: SandboxRunner | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._sandbox = sandbox or SubprocessRunner()
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "mypy_types"

    def validate(self, repo: Repository, patch: Patch) -> ValidationResult:
        py_files = _python_files_in_patch(patch)
        if not py_files:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.PASS,
                summary="no Python files in patch",
            )

        try:
            result = self._sandbox.run(
                [
                    "mypy",
                    "--no-error-summary",
                    "--no-pretty",
                    "--show-error-codes",
                    *py_files,
                ],
                cwd=repo.root,
                timeout=self._timeout,
            )
        except SandboxTimeoutError as exc:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.WARN,
                summary=f"mypy timed out after {self._timeout}s",
                details={"error": str(exc)},
            )
        except SandboxError as exc:
            return _tool_missing_or_reraise(self.name, exc)

        stdout = result.stdout.decode("utf-8", errors="replace")
        problems = _parse_mypy(stdout)

        errors = [p for p in problems if p["severity"] == "error"]
        warnings = [p for p in problems if p["severity"] == "warning"]

        if result.returncode == 0 and not errors:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.PASS,
                summary=f"mypy clean ({len(py_files)} file(s) checked)",
                details={"warnings": warnings} if warnings else {},
            )

        if errors:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.FAIL,
                summary=f"mypy reported {len(errors)} error(s)",
                details={"errors": errors[:50], "warnings": warnings[:50]},
            )

        # Non-zero exit but no errors parsed — likely a fatal we couldn't
        # interpret. Surface as WARN.
        return ValidationResult(
            validator=self.name,
            verdict=Verdict.WARN,
            summary=f"mypy returned {result.returncode} with no parsed errors",
            details={
                "stdout_head": stdout[:500],
                "stderr_head": result.stderr.decode("utf-8", errors="replace")[:500],
            },
        )


def _parse_mypy(text: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue
        m = _MYPY_RE.match(line)
        if m is None:
            continue
        out.append(
            {
                "file": m.group("file"),
                "line": int(m.group("line")),
                "severity": m.group("sev"),
                "message": m.group("msg"),
                "code": m.group("code"),
            }
        )
    return out


__all__ = ["MypyTypeValidator"]
