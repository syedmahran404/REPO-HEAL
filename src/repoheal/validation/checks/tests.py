"""Pytest unit-test validator.

Runs the repo's own test suite inside the sandbox. Unlike ruff/mypy,
this validator does *not* scope to patch files — running only "the
tests for changed code" requires reverse coverage data we don't have.
We run the full suite; the operator can configure timeouts and limits.

Verdict policy:

* Pytest exits 0 → ``PASS``.
* Pytest exits 5 (no tests collected) → ``PASS`` with a note.
* Pytest exits 1 (failures) → ``FAIL`` with structured failure list.
* Pytest exits 2/3/4 (collection / interrupt / internal error) → ``ERROR``.
* Tool missing → ``WARN``.
* Timeout → ``WARN``.

Pytest's machine-readable output via ``--junitxml`` could give us
richer failure data; for Phase 2 we parse the human summary line, which
is enough for the verdict and for surfacing failed test ids.
"""

from __future__ import annotations

import re

from ...core.models import Patch, Repository, ValidationResult, Verdict
from ...core.protocols import SandboxRunner
from ...exceptions import SandboxError, SandboxTimeoutError
from ...sandbox import SubprocessRunner
from .lint import _tool_missing_or_reraise

# Final-line summary regex: `1 failed, 6 passed in 0.42s`,
# or `5 passed, 1 skipped in 0.10s`, etc.
_SUMMARY_RE = re.compile(
    r"=+\s*(?P<body>(?:\d+\s+\w+(?:,\s*)?)+)\s+in\s+[\d\.]+s.*$"
)
_PART_RE = re.compile(r"(?P<count>\d+)\s+(?P<kind>\w+)")

# Failed-test header: `FAILED tests/test_x.py::test_thing - AssertionError: ...`
_FAILED_RE = re.compile(r"^FAILED\s+(?P<nodeid>\S+)(?:\s+-\s+(?P<reason>.*))?$")


class PytestValidator:
    """Run ``pytest`` and turn the result into a ValidationResult."""

    def __init__(
        self,
        *,
        sandbox: SandboxRunner | None = None,
        timeout: float = 600.0,
        max_failures: int = 5,
    ) -> None:
        self._sandbox = sandbox or SubprocessRunner()
        self._timeout = timeout
        self._max_failures = max_failures

    @property
    def name(self) -> str:
        return "pytest"

    def validate(self, repo: Repository, patch: Patch) -> ValidationResult:
        try:
            result = self._sandbox.run(
                [
                    "pytest",
                    "--tb=line",
                    "--no-header",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    f"--maxfail={self._max_failures}",
                ],
                cwd=repo.root,
                timeout=self._timeout,
            )
        except SandboxTimeoutError as exc:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.WARN,
                summary=f"pytest timed out after {self._timeout}s",
                details={"error": str(exc)},
            )
        except SandboxError as exc:
            return _tool_missing_or_reraise(self.name, exc)

        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        summary = _parse_summary(stdout)
        failed_tests = _parse_failed_tests(stdout)

        rc = result.returncode

        if rc == 0:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.PASS,
                summary=_summary_text(summary, default="all tests passed"),
                details={"counts": summary},
            )

        if rc == 5:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.PASS,
                summary="pytest collected no tests",
                details={"counts": summary},
            )

        if rc == 1:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.FAIL,
                summary=_summary_text(summary, default="tests failed"),
                details={
                    "counts": summary,
                    "failed": failed_tests[: self._max_failures],
                },
            )

        # 2 = collection / config error, 3 = interrupted, 4 = internal.
        return ValidationResult(
            validator=self.name,
            verdict=Verdict.ERROR,
            summary=f"pytest exited with code {rc}",
            details={
                "stdout_head": stdout[:1000],
                "stderr_head": stderr[:1000],
            },
        )


# --- helpers --------------------------------------------------------------


def _parse_summary(text: str) -> dict[str, int]:
    """Pull ``{passed:5, failed:1, skipped:0, ...}`` out of pytest output."""
    out: dict[str, int] = {}
    # Prefer the LAST summary line in the output (pytest can emit multiple).
    matches = list(_SUMMARY_RE.finditer(text))
    if not matches:
        return out
    body = matches[-1].group("body")
    for m in _PART_RE.finditer(body):
        try:
            out[m.group("kind")] = int(m.group("count"))
        except ValueError:
            continue
    return out


def _parse_failed_tests(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for line in text.splitlines():
        m = _FAILED_RE.match(line.strip())
        if m:
            out.append(
                {
                    "nodeid": m.group("nodeid"),
                    "reason": (m.group("reason") or "")[:300],
                }
            )
    return out


def _summary_text(counts: dict[str, int], *, default: str) -> str:
    if not counts:
        return default
    parts = [f"{n} {k}" for k, n in counts.items()]
    return ", ".join(parts)


__all__ = ["PytestValidator"]
