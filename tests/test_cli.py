"""Tests for the Typer CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from repoheal.cli import app

pytest.importorskip("tree_sitter_languages")


def test_version_command() -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert res.stdout.strip()


def test_ingest_command(tiny_repo: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["ingest", "--path", str(tiny_repo)])
    assert res.exit_code == 0, res.stdout
    assert "tiny_repo" in res.stdout


def test_scan_command_emits_finding(tiny_repo: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["scan", "--path", str(tiny_repo), "--rule", "circular_imports"])
    assert res.exit_code == 0, res.stdout
    assert "circular_imports" in res.stdout
