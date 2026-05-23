"""Syntax validator for Python.

Uses :func:`ast.parse` because the answer it gives is precisely what we
need: "would the Python interpreter accept this file at all?". Tools
like ruff add a lot of value but they're stricter than Python itself,
which makes them the *next* validator, not this one.
"""

from __future__ import annotations

import ast

from ...core.models import Language, Patch, Repository, ValidationResult, Verdict


class SyntaxValidator:
    """Verify that every Python file in the patch parses."""

    @property
    def name(self) -> str:
        return "python_syntax"

    def validate(self, repo: Repository, patch: Patch) -> ValidationResult:
        problems: list[dict[str, object]] = []
        checked = 0

        # We rely on the fact that, by the time the pipeline calls us,
        # the applier has written the patch to disk. So we read from
        # disk, not from ``edit.new_content``.
        for edit in patch.edits:
            if edit.is_deletion:
                continue
            if not _looks_python(edit.file.suffix):
                continue
            target = repo.root / edit.file
            try:
                source = target.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                problems.append(
                    {"file": str(edit.file), "error": f"could not read: {exc}"}
                )
                continue

            try:
                ast.parse(source, filename=str(edit.file))
                checked += 1
            except SyntaxError as exc:
                problems.append(
                    {
                        "file": str(edit.file),
                        "line": exc.lineno,
                        "col": exc.offset,
                        "msg": exc.msg,
                    }
                )

        if problems:
            return ValidationResult(
                validator=self.name,
                verdict=Verdict.FAIL,
                summary=f"{len(problems)} file(s) failed Python syntax check",
                details={"problems": problems},
            )
        return ValidationResult(
            validator=self.name,
            verdict=Verdict.PASS,
            summary=f"{checked} Python file(s) parsed successfully",
        )


# --- helpers -------------------------------------------------------------


def _looks_python(suffix: str) -> bool:
    return suffix.lower() in {".py", ".pyi"}


# We accept a lonely Language import path-wise to keep typing clean.
_ = Language  # noqa: B018 — module-level reference, intentional
