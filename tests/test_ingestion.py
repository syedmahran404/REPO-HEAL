"""Tests for the ingestion subsystem."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoheal.core.models import BuildSystem, Language
from repoheal.exceptions import IngestionError
from repoheal.ingestion import (
    EcosystemDetector,
    IngestionService,
    LocalPathCloner,
    RepositoryWalker,
)


# --- detector --------------------------------------------------------------


def test_detector_recognises_python_with_pyproject(tiny_repo: Path) -> None:
    eco = EcosystemDetector().detect(tiny_repo)
    assert Language.PYTHON in eco.languages
    # pyproject.toml is present, so PIP and POETRY (because of [tool.poetry])
    # should both be flagged.
    assert BuildSystem.PIP in eco.build_systems
    assert BuildSystem.POETRY in eco.build_systems


def test_detector_returns_empty_for_missing_root(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    eco = EcosystemDetector().detect(missing)
    assert eco.languages == ()
    assert eco.primary_language == Language.UNKNOWN


def test_detector_handles_empty_dir(tmp_path: Path) -> None:
    eco = EcosystemDetector().detect(tmp_path)
    assert eco.languages == ()


# --- walker ----------------------------------------------------------------


def test_walker_yields_python_files(tiny_repo: Path) -> None:
    files = list(RepositoryWalker().walk(tiny_repo))
    paths = {f.path.as_posix() for f in files}

    # The deliberate Python files should appear.
    assert "pkg/__init__.py" in paths
    assert "pkg/a.py" in paths
    assert "pkg/b.py" in paths
    assert "pkg/standalone.py" in paths

    # The README is text, gets a FileRef but with UNKNOWN language.
    readme = next((f for f in files if f.path.name == "README.md"), None)
    assert readme is not None
    assert readme.language == Language.UNKNOWN


def test_walker_respects_gitignore(tiny_repo: Path) -> None:
    files = list(RepositoryWalker().walk(tiny_repo))
    paths = {f.path.as_posix() for f in files}
    # ignored_dir/ is in .gitignore.
    assert all("ignored_dir" not in p for p in paths)


def test_walker_marks_python_extension_as_python(tiny_repo: Path) -> None:
    a_py = next(
        f for f in RepositoryWalker().walk(tiny_repo) if f.path.as_posix() == "pkg/a.py"
    )
    assert a_py.language == Language.PYTHON
    assert a_py.is_binary is False
    assert a_py.size_bytes > 0


def test_walker_paths_are_relative(tiny_repo: Path) -> None:
    for f in RepositoryWalker().walk(tiny_repo):
        assert not f.path.is_absolute()


def test_walker_raises_for_non_directory(tmp_path: Path) -> None:
    fake = tmp_path / "not_a_dir.txt"
    fake.write_text("hi")
    with pytest.raises(NotADirectoryError):
        list(RepositoryWalker().walk(fake))


# --- service ---------------------------------------------------------------


def test_ingestion_service_local_path(tiny_repo: Path) -> None:
    svc = IngestionService()
    repo = svc.ingest(tiny_repo)
    assert repo.name == "tiny_repo"
    assert repo.root == tiny_repo.resolve()
    assert repo.origin is None  # local path, no clone
    assert repo.file_count >= 4  # at least the four python files
    assert Language.PYTHON in repo.ecosystem.languages


def test_ingestion_service_rejects_unknown_source(tmp_path: Path) -> None:
    svc = IngestionService()
    bogus = tmp_path / "does_not_exist"
    with pytest.raises(IngestionError):
        svc.ingest(bogus)


def test_local_cloner_returns_resolved_path(tiny_repo: Path) -> None:
    cloned = LocalPathCloner().clone(str(tiny_repo))
    assert cloned == tiny_repo.resolve()
    assert cloned.is_dir()
