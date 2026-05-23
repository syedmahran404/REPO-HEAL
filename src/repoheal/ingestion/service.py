"""Ingestion orchestrator.

Composes the cloner + detector + walker into a single
``IngestionService`` that produces a fully-populated
:class:`~repoheal.core.models.Repository`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..core.models import Repository
from ..core.protocols import EcosystemDetector as EcosystemDetectorProto
from ..core.protocols import RepositoryCloner, RepositoryWalker as RepositoryWalkerProto
from ..exceptions import IngestionError
from ..logging import get_logger
from .cloner import GitCloner, LocalPathCloner, _looks_like_git_url
from .detector import EcosystemDetector
from .walker import RepositoryWalker

_log = get_logger(__name__)


class IngestionService:
    """High-level ingestion entry point.

    The service is constructed with the three Protocol-shaped components
    it needs. Default values produce a working production setup; tests
    inject fakes."""

    def __init__(
        self,
        *,
        git_cloner: RepositoryCloner | None = None,
        local_cloner: RepositoryCloner | None = None,
        detector: EcosystemDetectorProto | None = None,
        walker: RepositoryWalkerProto | None = None,
    ) -> None:
        self._git_cloner = git_cloner or GitCloner()
        self._local_cloner = local_cloner or LocalPathCloner()
        self._detector = detector or EcosystemDetector()
        self._walker = walker or RepositoryWalker()

    # -- public --------------------------------------------------------

    def ingest(
        self,
        source: str | Path,
        *,
        branch: str | None = None,
        name: str | None = None,
    ) -> Repository:
        """Ingest a repository from a path or a git URL."""
        source_str = str(source)
        cloner = self._pick_cloner(source_str)

        root = cloner.clone(source_str, branch=branch)
        _log.info("ingest.clone_done", source=source_str, root=str(root))

        ecosystem = self._detector.detect(root)
        _log.info(
            "ingest.detect_done",
            languages=[l.value for l in ecosystem.languages],
            build_systems=[b.value for b in ecosystem.build_systems],
        )

        files = list(self._walker.walk(root))
        _log.info("ingest.walk_done", file_count=len(files))

        repo = Repository(
            name=name or root.name,
            root=root,
            origin=source_str if cloner is self._git_cloner else None,
            branch=branch,
            commit=_resolve_commit(root) if cloner is self._git_cloner else None,
            ecosystem=ecosystem,
            files=files,
        )
        _log.info(
            "ingest.done",
            repo_id=str(repo.id),
            name=repo.name,
            file_count=repo.file_count,
        )
        return repo

    # -- helpers -------------------------------------------------------

    def _pick_cloner(self, source: str) -> RepositoryCloner:
        if _looks_like_git_url(source):
            return self._git_cloner
        if Path(source).expanduser().exists():
            return self._local_cloner
        raise IngestionError(
            f"could not determine ingestion mode for {source!r}: "
            "not a git URL and not an existing local path"
        )


def _resolve_commit(root: Path) -> str | None:
    """Best-effort short HEAD sha lookup for an on-disk repo."""
    try:
        out = subprocess.run(  # noqa: S603, S607
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", errors="replace").strip() or None
