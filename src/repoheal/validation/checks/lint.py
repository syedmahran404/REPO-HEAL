"""Ruff lint validator.

Wraps ``ruff check --output-format=json`` running inside the sandbox.
Scopes the check to the files actually touched by the patch — we have
no business reporting pre-existing issues unrelated to the patch.

Verdict policy:

* No Python files in patch → ``PASS`` (nothing to check).
* Ruff exits 0 → ``PASS``.
* Ruff exits non-zero with parseable JSON → ``FAIL`` (default) with the
  list of issues. The class accepts an ``issue_verdict`` argument so
  callers who want non-fatal lint can pass ``Verdict.WARN``.
* Ruff exits non-zero but JSON is unparseable → ``WARN`` with the raw
  output (avoids spurious blocks when the upstream tool changes its
  output format).
* Tool not installed in the sandbox → ``WARN`` with a clear message.
  This is *not* ``FAIL`` because we don't want to block patches just
  because the developer forgot to install ruff; the pipeline can be
  configured to escalate if the operator wants strict tooling.
* Tool timed out → ``WARN``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from ...core.models import Patch, Repository, ValidationResult, Verdict
from ...core.protocols import SandboxRunner
from ...exceptions import SandboxError, SandboxTimeoutError
from ...sandbox import SubprocessRunner


class RuffLintValidator:
    """Run ``ruff check`` against the patch's Python files."""

    def __init__(
        self,
        *,
        sandbox: SandboxRunner | None = None,
        timeout: float = 60.0,
        issue_verdict: Verdict = Verdict.FAIL,
    ) -> None:
        self._sandbox = sandbox or SubprocessRunner()
        self._timeout = timeout
        self._issue_verdict = issue_verdict

    @property
    def name(self) -> str:
        return "ruff_lint"

    # ------------------------------------------------------------------

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
                ["ruff", "check", "--no-fix", "--output-format=json", *py_files],
                cwd=repo.root,
                timeout=self._timeout,
            )
        except SandboxTimeoutError as exc:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.WARN,
                summary=f"ruff timed out after {self._timeout}s",
                details={"error": str(exc)},
            )
        except SandboxError as exc:
            return _tool_missing_or_reraise(self.name, exc)

        if result.returncode == 0:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.PASS,
                summary=f"ruff clean ({len(py_files)} file(s) checked)",
            )

        stdout = result.stdout.decode("utf-8", errors="replace")
        try:
            issues = json.loads(stdout)
        except json.JSONDecodeError:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.WARN,
                summary="ruff returned non-zero but output was not parseable JSON",
                details={
                    "returncode": result.returncode,
                    "stdout_head": stdout[:500],
                    "stderr_head": result.stderr.decode("utf-8", errors="replace")[:500],
                },
            )

        if not issues:
            # Non-zero return code but no parsed issues — trust the code.
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.WARN,
                summary=f"ruff returned {result.returncode} with no issues",
            )

        # Compact problem list for the report; trim each message.
        problems = [
            {
                "file": issue.get("filename"),
                "line": issue.get("location", {}).get("row"),
                "code": issue.get("code"),
                "message": issue.get("message", "")[:200],
            }
            for issue in issues
        ]
        return ValidationResult(
            validator=self.name,
            verdict=self._issue_verdict,
            summary=f"ruff found {len(problems)} issue(s)",
            details={"problems": problems},
        )


# --- helpers shared by validators in this package --------------------------


def _python_files_in_patch(patch: Patch) -> list[str]:
    out: list[str] = []
    for edit in patch.edits:
        if edit.is_deletion:
            continue
        if edit.file.suffix in {".py", ".pyi"}:
            out.append(str(edit.file))
    return out


def _tool_missing_or_reraise(validator_name: str, exc: SandboxError) -> ValidationResult:
    """Translate the conventional 'command not found' SandboxError into a
    WARN result; re-raise anything else."""
    msg = str(exc).lower()
    if "not found" in msg or "no such file" in msg:
        return ValidationResult(
            validator=validator_name,
            verdict=Verdict.WARN,
            summary=str(exc),
        )
    raise exc


__all__ = ["RuffLintValidator"]
