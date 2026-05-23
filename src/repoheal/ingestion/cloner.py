"""Cloners: bring a repository onto local disk in a known state.

Two implementations:

* :class:`LocalPathCloner` — for repositories already on disk. The
  "clone" is a path resolution; no copying. Useful for development and
  tests against fixture repos.

* :class:`GitCloner` — wraps the ``git`` CLI via :mod:`subprocess`. Uses
  shallow clones by default. Honors timeout and retry policy from
  :class:`~repoheal.config.Settings`.

Both implement :class:`~repoheal.core.protocols.RepositoryCloner`.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from ..config import Settings, get_settings
from ..exceptions import CloneError, IngestionError
from ..logging import get_logger

_log = get_logger(__name__)


class LocalPathCloner:
    """No-op cloner for repositories that already exist on local disk."""

    def clone(self, source: str, *, branch: str | None = None) -> Path:
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise IngestionError(f"local path does not exist: {path}")
        if not path.is_dir():
            raise IngestionError(f"local path is not a directory: {path}")
        if branch is not None:
            _log.debug(
                "local_path_cloner.branch_ignored",
                path=str(path),
                requested_branch=branch,
            )
        return path


class GitCloner:
    """Production cloner using the ``git`` CLI.

    Parameters
    ----------
    settings:
        Optional settings override (mostly for tests). When ``None``,
        process settings are used.
    workdir_factory:
        Optional callable returning the parent directory for clones.
        Defaults to ``settings.workdir / "clones"``.
    max_attempts:
        Number of clone attempts on transient failures. Backoff is
        exponential with base 1s.
    """

    _TRANSIENT_MARKERS: Sequence[str] = (
        "Could not resolve host",
        "Connection timed out",
        "Connection reset by peer",
        "early EOF",
        "RPC failed",
    )

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        max_attempts: int = 3,
    ) -> None:
        self._settings = settings or get_settings()
        self._max_attempts = max_attempts

    # -- public --------------------------------------------------------

    def clone(self, source: str, *, branch: str | None = None) -> Path:
        if not _looks_like_git_url(source):
            raise IngestionError(f"not a recognised git URL: {source!r}")

        target = self._fresh_target(source)
        last_error: str | None = None

        for attempt in range(1, self._max_attempts + 1):
            cmd = self._build_command(source, target, branch=branch)
            _log.info(
                "git.clone.start",
                source=source,
                target=str(target),
                attempt=attempt,
                branch=branch,
            )
            try:
                completed = subprocess.run(  # noqa: S603 - controlled cmd list
                    cmd,
                    capture_output=True,
                    timeout=self._settings.git_timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                last_error = f"timeout after {self._settings.git_timeout_seconds}s"
                _log.warning(
                    "git.clone.timeout", source=source, attempt=attempt, error=str(exc)
                )
                _safe_rmtree(target)
                self._sleep_backoff(attempt)
                continue

            if completed.returncode == 0:
                _log.info("git.clone.ok", source=source, target=str(target))
                return target

            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            last_error = stderr or f"git exited with code {completed.returncode}"
            _log.warning(
                "git.clone.failed",
                source=source,
                attempt=attempt,
                returncode=completed.returncode,
                stderr=stderr,
            )
            _safe_rmtree(target)

            if not self._is_transient(stderr):
                break  # permanent error, no point retrying

            self._sleep_backoff(attempt)

        raise CloneError(
            f"git clone failed for {source!r} after {self._max_attempts} attempts: "
            f"{last_error}"
        )

    # -- helpers -------------------------------------------------------

    def _fresh_target(self, source: str) -> Path:
        parent = self._settings.workdir / "clones"
        parent.mkdir(parents=True, exist_ok=True)
        # Suffix with a uuid so concurrent clones never collide.
        slug = _slugify(source)
        return parent / f"{slug}-{uuid4().hex[:8]}"

    @staticmethod
    def _build_command(source: str, target: Path, *, branch: str | None) -> list[str]:
        cmd = [
            "git",
            "clone",
            "--depth=1",
            "--single-branch",
            "--no-tags",
        ]
        if branch:
            cmd += ["--branch", branch]
        cmd += [source, str(target)]
        return cmd

    def _is_transient(self, stderr: str) -> bool:
        return any(marker in stderr for marker in self._TRANSIENT_MARKERS)

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(min(16, 4 ** (attempt - 1)))


# --- helpers -----------------------------------------------------------------


def _looks_like_git_url(source: str) -> bool:
    """Cheap heuristic. We don't want to call out to git just to validate."""
    if source.startswith(("http://", "https://", "git@", "ssh://", "git://")):
        return True
    return source.endswith(".git")


def _slugify(source: str) -> str:
    """Make a safe directory-name fragment out of a clone source."""
    keep = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    base = source.rstrip("/").split("/")[-1].removesuffix(".git") or "repo"
    return "".join(c if c in keep else "-" for c in base)[:48] or "repo"


def _safe_rmtree(path: Path) -> None:
    """Best-effort remove; logs but never raises."""
    if path.exists():
        try:
            shutil.rmtree(path)
        except OSError as exc:
            _log.warning("rmtree.failed", path=str(path), error=str(exc))
