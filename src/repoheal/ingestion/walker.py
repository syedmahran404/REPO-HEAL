"""gitignore-aware repository walker.

Yields :class:`~repoheal.core.models.FileRef` for each in-scope source
file. Out of scope:

* anything matched by ``.gitignore`` (compiled with ``pathspec``);
* anything inside a ``skip_dirs`` set (``.git``, ``node_modules``,
  virtualenvs, build outputs, caches);
* binary files (detected by null-byte scan of the leading 8 KiB);
* files larger than ``settings.max_file_size_bytes`` (recorded with
  ``size_bytes`` but skipped by parsers; we still emit the ``FileRef``
  so detection rules can reason about size).

Symlinks are followed only when they point inside ``root``. This
prevents a malicious symlink from leaking the walker into ``/etc``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

import pathspec

from ..config import Settings, get_settings
from ..core.models import FileRef, Language
from ..logging import get_logger
from .detector import _EXTENSION_TABLE  # internal, intentional reuse

_log = get_logger(__name__)

# Directories we never descend into. Conservative default; project-specific
# tuning belongs in .gitignore, not here.
_DEFAULT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "node_modules",
        "venv",
        ".venv",
        "env",
        ".env.d",
        "__pycache__",
        "build",
        "dist",
        "target",
        ".gradle",
        ".terraform",
        "vendor",
    }
)

_BINARY_SCAN_BYTES = 8192


class RepositoryWalker:
    """Iterate over the in-scope source files of a repository."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        skip_dirs: Iterable[str] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._skip_dirs = frozenset(skip_dirs) if skip_dirs is not None else _DEFAULT_SKIP_DIRS

    def walk(self, root: Path) -> Iterator[FileRef]:
        root = root.resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {root}")

        spec = self._load_gitignore(root)

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(dirpath)

            # Prune ignored / skip dirs in-place so os.walk does not descend.
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d not in self._skip_dirs
                and not _is_gitignored(spec, root, current / d, is_dir=True)
            )

            for fname in sorted(filenames):
                fpath = current / fname
                if _is_gitignored(spec, root, fpath, is_dir=False):
                    continue

                # Don't follow out-of-tree symlinks.
                try:
                    if fpath.is_symlink():
                        target = fpath.resolve()
                        if not _is_subpath(target, root):
                            _log.debug(
                                "walker.skip_external_symlink",
                                path=str(fpath),
                                target=str(target),
                            )
                            continue
                except OSError:
                    continue

                file_ref = self._make_file_ref(root, fpath)
                if file_ref is not None:
                    yield file_ref

    # -- helpers --------------------------------------------------

    def _make_file_ref(self, root: Path, fpath: Path) -> FileRef | None:
        try:
            stat = fpath.stat()
        except OSError as exc:
            _log.debug("walker.stat_failed", path=str(fpath), error=str(exc))
            return None

        is_binary = _looks_binary(fpath)
        language = _language_from_path(fpath) if not is_binary else Language.UNKNOWN

        try:
            relative = fpath.relative_to(root)
        except ValueError:
            return None

        return FileRef(
            path=relative,
            language=language,
            size_bytes=stat.st_size,
            is_binary=is_binary,
        )

    def _load_gitignore(self, root: Path) -> pathspec.PathSpec | None:
        gi = root / ".gitignore"
        if not gi.exists():
            return None
        try:
            with gi.open("r", encoding="utf-8", errors="replace") as f:
                return pathspec.PathSpec.from_lines("gitwildmatch", f)
        except OSError as exc:
            _log.warning("walker.gitignore_read_failed", path=str(gi), error=str(exc))
            return None


# --- module-level helpers --------------------------------------------------


def _is_gitignored(
    spec: pathspec.PathSpec | None,
    root: Path,
    path: Path,
    *,
    is_dir: bool,
) -> bool:
    if spec is None:
        return False
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    s = rel.as_posix()
    if is_dir and not s.endswith("/"):
        s = s + "/"
    return spec.match_file(s)


def _is_subpath(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _looks_binary(path: Path) -> bool:
    """Cheap binary check: read up to 8 KiB and look for a NUL byte."""
    try:
        with path.open("rb") as f:
            chunk = f.read(_BINARY_SCAN_BYTES)
    except OSError:
        return True  # treat unreadable as binary, downstream will skip
    return b"\x00" in chunk


def _language_from_path(path: Path) -> Language:
    return _EXTENSION_TABLE.get(path.suffix.lower(), Language.UNKNOWN)
