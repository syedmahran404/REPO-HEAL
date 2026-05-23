"""Unified-diff generation for patches.

Produces a diff that's compatible with ``git apply`` and ``patch``.
We deliberately use ``a/`` and ``b/`` prefixes to match git's default
conventions; downstream tooling expects them.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from ..core.models import FileEdit, Patch, Repository


class UnifiedDiffGenerator:
    """Turn a Patch into a unified diff string."""

    def __init__(self, *, context_lines: int = 3) -> None:
        self._context_lines = context_lines

    # ------------------------------------------------------------------

    def generate(self, repo: Repository, patch: Patch) -> str:
        """Generate a unified diff for the entire patch."""
        chunks: list[str] = []
        for edit in patch.edits:
            chunks.append(self._diff_for_edit(repo, edit))
        return "".join(chunks)

    # ------------------------------------------------------------------

    def _diff_for_edit(self, repo: Repository, edit: FileEdit) -> str:
        target = repo.root / edit.file
        old_lines: list[str]
        new_lines: list[str]

        if edit.is_new_file:
            old_lines = []
            new_lines = _splitlines_keepends(edit.new_content)
            from_path = "/dev/null"
            to_path = f"b/{edit.file.as_posix()}"
        elif edit.is_deletion:
            old_lines = (
                _splitlines_keepends(_safe_read_text(target)) if target.exists() else []
            )
            new_lines = []
            from_path = f"a/{edit.file.as_posix()}"
            to_path = "/dev/null"
        else:
            old_lines = (
                _splitlines_keepends(_safe_read_text(target)) if target.exists() else []
            )
            new_lines = _splitlines_keepends(edit.new_content)
            from_path = f"a/{edit.file.as_posix()}"
            to_path = f"b/{edit.file.as_posix()}"

        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=from_path,
                tofile=to_path,
                n=self._context_lines,
            )
        )
        if not diff_lines:
            return ""

        # difflib doesn't emit a trailing newline on the last hunk if the
        # source didn't have one; ensure we end on a newline so the diff
        # concatenation is well-formed.
        out = "".join(diff_lines)
        if not out.endswith("\n"):
            out += "\n"
        return out


# --- helpers -------------------------------------------------------------


def _splitlines_keepends(s: str) -> list[str]:
    return s.splitlines(keepends=True) or ([""] if s == "" else [])


def _safe_read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
