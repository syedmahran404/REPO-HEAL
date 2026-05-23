"""Tests for the subprocess sandbox runner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from repoheal.exceptions import SandboxError, SandboxTimeoutError
from repoheal.sandbox import SubprocessRunner


def test_runner_runs_command_successfully(tmp_path: Path) -> None:
    res = SubprocessRunner().run(
        [sys.executable, "-c", "print('hello')"],
        cwd=tmp_path,
        timeout=10,
    )
    assert res.returncode == 0
    assert res.stdout.strip() == b"hello"
    assert res.succeeded is True


def test_runner_captures_nonzero_exit(tmp_path: Path) -> None:
    res = SubprocessRunner().run(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        cwd=tmp_path,
        timeout=10,
    )
    assert res.returncode == 3
    assert not res.succeeded


def test_runner_raises_on_timeout(tmp_path: Path) -> None:
    with pytest.raises(SandboxTimeoutError):
        SubprocessRunner().run(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=tmp_path,
            timeout=0.5,
        )


def test_runner_rejects_missing_cwd(tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        SubprocessRunner().run(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path / "missing",
            timeout=5,
        )


def test_runner_rejects_missing_command(tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        SubprocessRunner().run(
            ["definitely-not-a-command-xyz"],
            cwd=tmp_path,
            timeout=5,
        )


def test_runner_env_scrubs_unallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOHEAL_TEST_SECRET", "should-not-leak")
    res = SubprocessRunner().run(
        [
            sys.executable,
            "-c",
            "import os, sys; sys.stdout.write(os.environ.get('REPOHEAL_TEST_SECRET', '<missing>'))",
        ],
        cwd=tmp_path,
        timeout=10,
    )
    # The secret env var is NOT in the allowlist, so the child should
    # not see it.
    assert res.stdout == b"<missing>"
