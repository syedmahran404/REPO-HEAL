"""Tests for the patching subsystem."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from repoheal.core.models import (
    Ecosystem,
    FileEdit,
    Patch,
    Repository,
)
from repoheal.exceptions import PatchError
from repoheal.patching import PatchApplier, UnifiedDiffGenerator


@pytest.fixture
def working_repo(tmp_path: Path, tiny_repo: Path) -> Repository:
    """A copy of tiny_repo we can mutate without affecting the original."""
    dst = tmp_path / "tiny_repo"
    shutil.copytree(tiny_repo, dst)
    return Repository(name="tiny_repo", root=dst, ecosystem=Ecosystem(), files=[])


# --- diff -----------------------------------------------------------------


def test_diff_for_modified_file(working_repo: Repository) -> None:
    edit = FileEdit(file=Path("pkg/a.py"), new_content="x = 1\n")
    patch = Patch(title="t", description="d", edits=(edit,))
    diff = UnifiedDiffGenerator().generate(working_repo, patch)
    assert "--- a/pkg/a.py" in diff
    assert "+++ b/pkg/a.py" in diff
    assert "+x = 1" in diff


def test_diff_for_new_file(working_repo: Repository) -> None:
    edit = FileEdit(file=Path("new.py"), new_content="hello\n", is_new_file=True)
    patch = Patch(title="t", description="d", edits=(edit,))
    diff = UnifiedDiffGenerator().generate(working_repo, patch)
    assert "--- /dev/null" in diff
    assert "+++ b/new.py" in diff


def test_diff_for_deletion(working_repo: Repository) -> None:
    edit = FileEdit(file=Path("pkg/a.py"), new_content="", is_deletion=True)
    patch = Patch(title="t", description="d", edits=(edit,))
    diff = UnifiedDiffGenerator().generate(working_repo, patch)
    assert "+++ /dev/null" in diff


# --- applier --------------------------------------------------------------


def test_applier_writes_and_can_rollback(working_repo: Repository) -> None:
    a_path = working_repo.root / "pkg" / "a.py"
    original = a_path.read_text()

    edit = FileEdit(file=Path("pkg/a.py"), new_content="REPLACED\n")
    patch = Patch(title="t", description="d", edits=(edit,))

    applier = PatchApplier()
    applier.apply(patch, working_repo)
    assert a_path.read_text() == "REPLACED\n"

    applier.rollback(working_repo)
    assert a_path.read_text() == original


def test_applier_rollback_after_failure_restores_original(
    working_repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    a_path = working_repo.root / "pkg" / "a.py"
    b_path = working_repo.root / "pkg" / "b.py"
    a_orig = a_path.read_text()
    b_orig = b_path.read_text()

    # First edit succeeds, second blows up.
    patch = Patch(
        title="t",
        description="d",
        edits=(
            FileEdit(file=Path("pkg/a.py"), new_content="A_NEW\n"),
            FileEdit(file=Path("pkg/b.py"), new_content="B_NEW\n"),
        ),
    )

    applier = PatchApplier()

    real_apply = applier._apply_one
    call_count = {"n": 0}

    def boom(repo, edit):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated failure")
        return real_apply(repo, edit)

    monkeypatch.setattr(applier, "_apply_one", boom)

    with pytest.raises(PatchError):
        applier.apply(patch, working_repo)

    # Both files must be back to their originals.
    assert a_path.read_text() == a_orig
    assert b_path.read_text() == b_orig


def test_applier_rolls_back_new_file_creation(working_repo: Repository) -> None:
    new_path = working_repo.root / "added.py"
    assert not new_path.exists()

    edit = FileEdit(file=Path("added.py"), new_content="x\n", is_new_file=True)
    patch = Patch(title="t", description="d", edits=(edit,))

    applier = PatchApplier()
    applier.apply(patch, working_repo)
    assert new_path.exists()

    applier.rollback(working_repo)
    assert not new_path.exists()


def test_applier_rollback_without_apply_raises(working_repo: Repository) -> None:
    with pytest.raises(PatchError):
        PatchApplier().rollback(working_repo)
