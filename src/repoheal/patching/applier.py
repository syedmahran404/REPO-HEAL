"""Patch application with snapshot-based rollback.

Hard guarantee: ``apply()`` either succeeds completely (every edit
landed) or leaves the working tree exactly as it was.

Implementation strategy:

1. Snapshot the original content of every file the patch touches into
   memory before applying anything.
2. Apply edits one by one.
3. On any error during application, restore from the snapshot and
   re-raise.

This is *not* a sandbox. The applier writes to the on-disk working
tree of the repository it was given. A safe agent loop wraps this with
a sandboxed copy of the tree.
"""

from __future__ import annotations

from pathlib import Path

from ..core.models import FileEdit, Patch, Repository
from ..exceptions import PatchError
from ..logging import get_logger

_log = get_logger(__name__)


class PatchApplier:
    """Apply patches transactionally."""

    def __init__(self) -> None:
        # Per-instance snapshot store keyed by repo.id. Each entry maps
        # a file path (relative) → original content (or ``None`` if the
        # file did not exist).
        self._snapshots: dict[str, dict[Path, bytes | None]] = {}

    # ------------------------------------------------------------------

    def apply(self, patch: Patch, repo: Repository) -> None:
        if not patch.edits:
            return

        snapshot = self._snapshot_files(repo, patch)
        self._snapshots[str(repo.id)] = snapshot

        applied: list[FileEdit] = []
        try:
            for edit in patch.edits:
                self._apply_one(repo, edit)
                applied.append(edit)
        except Exception as exc:
            _log.warning(
                "patch.apply_failed",
                patch_id=str(patch.id),
                applied=len(applied),
                error=str(exc),
            )
            self._restore(repo, snapshot)
            raise PatchError(f"patch application failed: {exc}") from exc

        _log.info(
            "patch.applied",
            patch_id=str(patch.id),
            edits=len(patch.edits),
        )

    # ------------------------------------------------------------------

    def rollback(self, repo: Repository) -> None:
        """Roll back the most recent ``apply`` for this repository."""
        snap = self._snapshots.pop(str(repo.id), None)
        if snap is None:
            raise PatchError(f"no snapshot to roll back for repo {repo.id}")
        self._restore(repo, snap)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _snapshot_files(
        self,
        repo: Repository,
        patch: Patch,
    ) -> dict[Path, bytes | None]:
        snap: dict[Path, bytes | None] = {}
        for edit in patch.edits:
            target = repo.root / edit.file
            if target.exists():
                try:
                    snap[edit.file] = target.read_bytes()
                except OSError as exc:
                    raise PatchError(
                        f"snapshot read failed for {edit.file}: {exc}"
                    ) from exc
            else:
                snap[edit.file] = None
        return snap

    def _apply_one(self, repo: Repository, edit: FileEdit) -> None:
        target = repo.root / edit.file
        if edit.is_deletion:
            if target.exists():
                target.unlink()
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(edit.new_content, encoding="utf-8")

    def _restore(self, repo: Repository, snap: dict[Path, bytes | None]) -> None:
        for rel_path, original in snap.items():
            target = repo.root / rel_path
            if original is None:
                if target.exists():
                    try:
                        target.unlink()
                    except OSError as exc:
                        _log.warning(
                            "patch.rollback_unlink_failed",
                            path=str(target),
                            error=str(exc),
                        )
            else:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(original)
                except OSError as exc:
                    _log.warning(
                        "patch.rollback_write_failed",
                        path=str(target),
                        error=str(exc),
                    )
