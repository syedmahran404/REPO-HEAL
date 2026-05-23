"""Tests for the validation pipeline."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from repoheal.core.models import (
    Ecosystem,
    FileEdit,
    Patch,
    Repository,
    ValidationResult,
    Verdict,
)
from repoheal.patching import PatchApplier
from repoheal.validation import SyntaxValidator, ValidationPipeline


@pytest.fixture
def working_repo(tmp_path: Path, tiny_repo: Path) -> Repository:
    dst = tmp_path / "tiny_repo"
    shutil.copytree(tiny_repo, dst)
    return Repository(name="tiny_repo", root=dst, ecosystem=Ecosystem(), files=[])


def _apply(patch: Patch, repo: Repository) -> None:
    PatchApplier().apply(patch, repo)


# --- SyntaxValidator ------------------------------------------------------


def test_syntax_validator_passes_for_valid_python(working_repo: Repository) -> None:
    edit = FileEdit(file=Path("pkg/a.py"), new_content="x: int = 1\n")
    patch = Patch(title="t", description="d", edits=(edit,))
    _apply(patch, working_repo)

    result = SyntaxValidator().validate(working_repo, patch)
    assert result.verdict == Verdict.PASS


def test_syntax_validator_fails_for_invalid_python(working_repo: Repository) -> None:
    edit = FileEdit(file=Path("pkg/a.py"), new_content="def broken( :\n")
    patch = Patch(title="t", description="d", edits=(edit,))
    _apply(patch, working_repo)

    result = SyntaxValidator().validate(working_repo, patch)
    assert result.verdict == Verdict.FAIL
    assert result.details["problems"]


def test_syntax_validator_skips_non_python(working_repo: Repository) -> None:
    edit = FileEdit(file=Path("README.md"), new_content="# garbage but valid markdown\n")
    patch = Patch(title="t", description="d", edits=(edit,))
    _apply(patch, working_repo)

    # No Python files in patch → PASS with zero checks.
    result = SyntaxValidator().validate(working_repo, patch)
    assert result.verdict == Verdict.PASS


# --- pipeline -------------------------------------------------------------


class _AlwaysWarns:
    @property
    def name(self) -> str:
        return "warns"

    def validate(self, repo, patch):  # type: ignore[no-untyped-def]
        return ValidationResult(validator=self.name, verdict=Verdict.WARN, summary="hmm")


class _AlwaysFails:
    @property
    def name(self) -> str:
        return "fails"

    def validate(self, repo, patch):  # type: ignore[no-untyped-def]
        return ValidationResult(validator=self.name, verdict=Verdict.FAIL, summary="nope")


class _Crashes:
    @property
    def name(self) -> str:
        return "crashes"

    def validate(self, repo, patch):  # type: ignore[no-untyped-def]
        raise RuntimeError("oh no")


def test_pipeline_pass_when_all_pass(working_repo: Repository) -> None:
    edit = FileEdit(file=Path("pkg/a.py"), new_content="ok = 1\n")
    patch = Patch(title="t", description="d", edits=(edit,))
    _apply(patch, working_repo)

    report = ValidationPipeline([SyntaxValidator()]).run(working_repo, patch)
    assert report.passed
    assert report.overall == Verdict.PASS
    assert all(r.verdict == Verdict.PASS for r in report.results)


def test_pipeline_warn_when_any_warn(working_repo: Repository) -> None:
    edit = FileEdit(file=Path("pkg/a.py"), new_content="ok = 1\n")
    patch = Patch(title="t", description="d", edits=(edit,))
    _apply(patch, working_repo)

    report = ValidationPipeline([SyntaxValidator(), _AlwaysWarns()]).run(working_repo, patch)
    assert report.overall == Verdict.WARN


def test_pipeline_short_circuits_on_fail(working_repo: Repository) -> None:
    edit = FileEdit(file=Path("pkg/a.py"), new_content="ok = 1\n")
    patch = Patch(title="t", description="d", edits=(edit,))
    _apply(patch, working_repo)

    report = ValidationPipeline([_AlwaysFails(), SyntaxValidator()]).run(working_repo, patch)
    assert report.overall == Verdict.FAIL
    # Only the failing validator ran.
    assert len(report.results) == 1
    assert report.results[0].validator == "fails"


def test_pipeline_treats_validator_crash_as_fail(working_repo: Repository) -> None:
    edit = FileEdit(file=Path("pkg/a.py"), new_content="ok = 1\n")
    patch = Patch(title="t", description="d", edits=(edit,))
    _apply(patch, working_repo)

    report = ValidationPipeline([_Crashes()]).run(working_repo, patch)
    assert report.overall == Verdict.FAIL
    assert report.results[0].verdict == Verdict.ERROR
