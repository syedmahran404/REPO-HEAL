"""Tests for the Phase 2 sandbox-backed validators.

Each validator is exercised with a ``FakeSandboxRunner`` that returns
canned :class:`SandboxResult`s so we don't need ``ruff`` / ``mypy`` /
``pytest`` actually installed in the test process.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from repoheal.core.models import (
    Ecosystem,
    FileEdit,
    Patch,
    Repository,
    ValidationResult,
    Verdict,
)
from repoheal.core.protocols import SandboxResult
from repoheal.exceptions import SandboxError, SandboxTimeoutError
from repoheal.validation import (
    MypyTypeValidator,
    PytestValidator,
    RuffLintValidator,
    ValidationPipeline,
    default_pipeline,
)


# --- fake sandbox -----------------------------------------------------------


class FakeSandboxRunner:
    """Records calls and returns canned results.

    Configure via ``add_result(tool, result)`` keyed on the FIRST token
    of the command (the executable name). Use ``add_error`` to make a
    given tool raise."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results: dict[str, SandboxResult] = {}
        self._errors: dict[str, BaseException] = {}

    def add_result(self, tool: str, *, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self._results[tool] = SandboxResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=0.01,
        )

    def add_error(self, tool: str, error: BaseException) -> None:
        self._errors[tool] = error

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> SandboxResult:
        self.calls.append(
            {"command": list(command), "cwd": str(cwd), "timeout": timeout}
        )
        tool = command[0] if command else ""
        if tool in self._errors:
            raise self._errors[tool]
        if tool in self._results:
            return self._results[tool]
        raise AssertionError(
            f"FakeSandboxRunner has no canned result for {tool!r} "
            f"(command={list(command)})"
        )


# --- helpers ---------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    return Repository(name="r", root=tmp_path, ecosystem=Ecosystem(), files=[])


def _patch(*files: str) -> Patch:
    edits = tuple(
        FileEdit(file=Path(f), new_content="x = 1\n")
        for f in files
    )
    return Patch(title="t", description="d", edits=edits)


# =============================================================================
# RuffLintValidator
# =============================================================================


def test_ruff_pass_when_clean(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    sandbox.add_result("ruff", returncode=0)
    v = RuffLintValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.PASS
    assert sandbox.calls[0]["command"][0] == "ruff"


def test_ruff_fail_with_parsed_issues(repo: Repository) -> None:
    issues = [
        {
            "filename": str(repo.root / "a.py"),
            "code": "F401",
            "message": "Unused import",
            "location": {"row": 3, "column": 1},
        }
    ]
    sandbox = FakeSandboxRunner()
    sandbox.add_result("ruff", returncode=1, stdout=json.dumps(issues).encode())
    v = RuffLintValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.FAIL
    problems = result.details["problems"]
    assert problems[0]["code"] == "F401"
    assert problems[0]["line"] == 3


def test_ruff_warn_on_unparseable_output(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    sandbox.add_result("ruff", returncode=1, stdout=b"<<not-json>>")
    v = RuffLintValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.WARN


def test_ruff_warn_when_tool_missing(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    sandbox.add_error("ruff", SandboxError("command not found: ruff"))
    v = RuffLintValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.WARN
    assert "not found" in result.summary.lower()


def test_ruff_warn_on_timeout(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    sandbox.add_error("ruff", SandboxTimeoutError("ruff timed out"))
    v = RuffLintValidator(sandbox=sandbox, timeout=0.1)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.WARN
    assert "timed out" in result.summary


def test_ruff_pass_when_no_python_files(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()  # no canned results — should not be called
    v = RuffLintValidator(sandbox=sandbox)
    patch = Patch(
        title="t",
        description="d",
        edits=(FileEdit(file=Path("README.md"), new_content="hi"),),
    )
    result = v.validate(repo, patch)
    assert result.verdict == Verdict.PASS
    assert sandbox.calls == []  # short-circuited


def test_ruff_issue_verdict_configurable(repo: Repository) -> None:
    issues = [{"filename": "a.py", "code": "F401", "message": "x", "location": {"row": 1, "column": 1}}]
    sandbox = FakeSandboxRunner()
    sandbox.add_result("ruff", returncode=1, stdout=json.dumps(issues).encode())
    v = RuffLintValidator(sandbox=sandbox, issue_verdict=Verdict.WARN)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.WARN


# =============================================================================
# MypyTypeValidator
# =============================================================================


def test_mypy_pass_when_no_errors(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    sandbox.add_result("mypy", returncode=0)
    v = MypyTypeValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.PASS


def test_mypy_fail_on_errors(repo: Repository) -> None:
    output = (
        "a.py:10: error: Incompatible types in assignment  [assignment]\n"
        "a.py:20:5: error: Missing return statement  [return]\n"
        "a.py:25: note: Recipe...\n"
    )
    sandbox = FakeSandboxRunner()
    sandbox.add_result("mypy", returncode=1, stdout=output.encode())
    v = MypyTypeValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.FAIL
    assert len(result.details["errors"]) == 2
    assert result.details["errors"][0]["code"] == "assignment"


def test_mypy_warn_when_tool_missing(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    sandbox.add_error("mypy", SandboxError("command not found"))
    v = MypyTypeValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.WARN


def test_mypy_pass_with_warnings_only(repo: Repository) -> None:
    output = "a.py:1: warning: deprecated something\n"
    sandbox = FakeSandboxRunner()
    sandbox.add_result("mypy", returncode=0, stdout=output.encode())
    v = MypyTypeValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.PASS
    assert len(result.details["warnings"]) == 1


def test_mypy_pass_no_python_files(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    v = MypyTypeValidator(sandbox=sandbox)
    patch = Patch(
        title="t",
        description="d",
        edits=(FileEdit(file=Path("README.md"), new_content="hi"),),
    )
    result = v.validate(repo, patch)
    assert result.verdict == Verdict.PASS
    assert sandbox.calls == []


# =============================================================================
# PytestValidator
# =============================================================================


def test_pytest_pass(repo: Repository) -> None:
    output = "===== 5 passed in 0.10s =====\n"
    sandbox = FakeSandboxRunner()
    sandbox.add_result("pytest", returncode=0, stdout=output.encode())
    v = PytestValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.PASS
    assert result.details["counts"] == {"passed": 5}


def test_pytest_fail_with_failed_tests(repo: Repository) -> None:
    output = (
        "FAILED tests/test_x.py::test_a - AssertionError: 1 != 2\n"
        "FAILED tests/test_x.py::test_b - ValueError: bad\n"
        "===== 2 failed, 3 passed in 0.20s =====\n"
    )
    sandbox = FakeSandboxRunner()
    sandbox.add_result("pytest", returncode=1, stdout=output.encode())
    v = PytestValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.FAIL
    failed = result.details["failed"]
    assert len(failed) == 2
    assert failed[0]["nodeid"] == "tests/test_x.py::test_a"


def test_pytest_pass_when_no_tests_collected(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    sandbox.add_result("pytest", returncode=5, stdout=b"")
    v = PytestValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.PASS


def test_pytest_error_on_collection_error(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    sandbox.add_result("pytest", returncode=2, stderr=b"ERROR: invalid foo")
    v = PytestValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.ERROR


def test_pytest_warn_when_tool_missing(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    sandbox.add_error("pytest", SandboxError("command not found"))
    v = PytestValidator(sandbox=sandbox)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.WARN


def test_pytest_warn_on_timeout(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    sandbox.add_error("pytest", SandboxTimeoutError("pytest timed out"))
    v = PytestValidator(sandbox=sandbox, timeout=0.1)
    result = v.validate(repo, _patch("a.py"))
    assert result.verdict == Verdict.WARN


# =============================================================================
# default_pipeline integration
# =============================================================================


def test_default_pipeline_short_circuits_on_lint_fail(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    issues = [{"filename": "a.py", "code": "F401", "message": "x", "location": {"row": 1, "column": 1}}]
    sandbox.add_result("ruff", returncode=1, stdout=json.dumps(issues).encode())
    # mypy / pytest results NOT registered — pipeline must short-circuit.

    a_py = repo.root / "a.py"
    a_py.write_text("x = 1\n")  # syntax validator reads from disk

    pipeline = default_pipeline(sandbox=sandbox)
    report = pipeline.run(repo, _patch("a.py"))
    assert report.overall == Verdict.FAIL
    # First result is syntax (PASS), second is ruff (FAIL); pipeline stops there.
    names = [r.validator for r in report.results]
    assert names == ["python_syntax", "ruff_lint"]


def test_default_pipeline_passes_through_to_pytest(repo: Repository) -> None:
    sandbox = FakeSandboxRunner()
    sandbox.add_result("ruff", returncode=0)
    sandbox.add_result("mypy", returncode=0)
    sandbox.add_result("pytest", returncode=0, stdout=b"===== 3 passed in 0.01s =====")

    a_py = repo.root / "a.py"
    a_py.write_text("x = 1\n")

    pipeline = default_pipeline(sandbox=sandbox)
    report = pipeline.run(repo, _patch("a.py"))
    assert report.passed
    names = [r.validator for r in report.results]
    assert names == ["python_syntax", "ruff_lint", "mypy_types", "pytest"]
