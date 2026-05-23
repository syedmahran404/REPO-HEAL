"""Tests for the Phase 2 CLI subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repoheal.cli import app

pytest.importorskip("tree_sitter_languages")


# --- search -------------------------------------------------------------


def test_search_command_finds_function(medium_repo: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["search", "--path", str(medium_repo), "--query", "handle_request"])
    assert res.exit_code == 0, res.stdout
    assert "handle_request" in res.stdout


def test_search_command_with_top_k(medium_repo: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(
        app,
        ["search", "--path", str(medium_repo), "--query", "user", "--top-k", "3"],
    )
    assert res.exit_code == 0, res.stdout


# --- agent run ----------------------------------------------------------


def test_agent_list_shows_root_cause() -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["agent", "list"])
    assert res.exit_code == 0
    assert "root_cause" in res.stdout


def test_agent_run_root_cause(medium_repo: Path) -> None:
    runner = CliRunner()
    inputs = json.dumps(
        {"anchor_node": "function::pkg.services.handle_request", "max_depth": 3, "top_k": 5}
    )
    res = runner.invoke(
        app,
        [
            "agent",
            "run",
            "--path",
            str(medium_repo),
            "--agent",
            "root_cause",
            "--inputs",
            inputs,
            "--no-retrieval",
        ],
    )
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert payload["status"] == "ok"
    qnames = [h["qualified_name"] for h in payload["output"]["hypotheses"]]
    assert "pkg.api.public_endpoint" in qnames


def test_agent_run_unknown_agent_exits_nonzero(medium_repo: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(
        app,
        [
            "agent",
            "run",
            "--path",
            str(medium_repo),
            "--agent",
            "definitely-not-a-real-agent",
            "--inputs",
            "{}",
        ],
    )
    assert res.exit_code != 0
    assert "unknown agent" in res.stdout.lower() or "unknown agent" in (res.stderr or "").lower()


def test_agent_run_invalid_inputs_json(medium_repo: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(
        app,
        [
            "agent",
            "run",
            "--path",
            str(medium_repo),
            "--agent",
            "root_cause",
            "--inputs",
            "{not-json",
        ],
    )
    assert res.exit_code != 0


# --- trace --------------------------------------------------------------


def test_trace_command_correlates_frames(medium_repo: Path, tmp_path: Path) -> None:
    tb_file = tmp_path / "tb.txt"
    tb_file.write_text(
        'Traceback (most recent call last):\n'
        '  File "pkg/services.py", line 16, in handle_request\n'
        '    return user.name\n'
        "AttributeError: 'NoneType' object has no attribute 'name'\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    res = runner.invoke(
        app, ["trace", "--path", str(medium_repo), "--traceback", str(tb_file)]
    )
    assert res.exit_code == 0, res.stdout
    assert "AttributeError" in res.stdout
    assert "pkg.services.handle_request" in res.stdout


def test_trace_command_empty_traceback(medium_repo: Path, tmp_path: Path) -> None:
    tb_file = tmp_path / "empty.txt"
    tb_file.write_text("no frames here\n", encoding="utf-8")
    runner = CliRunner()
    res = runner.invoke(
        app, ["trace", "--path", str(medium_repo), "--traceback", str(tb_file)]
    )
    assert res.exit_code != 0
    assert "no frames" in res.stdout.lower()


# --- issue correlate ---------------------------------------------------


def test_issue_correlate_command(medium_repo: Path, tmp_path: Path) -> None:
    issue_file = tmp_path / "issue.json"
    issue_file.write_text(
        json.dumps(
            {
                "number": 1,
                "title": "Bug in handle_request",
                "body": (
                    'Traceback (most recent call last):\n'
                    '  File "pkg/services.py", line 16, in handle_request\n'
                    "AttributeError: 'NoneType' object has no attribute 'name'\n"
                ),
                "labels": ["bug"],
                "comments": [],
            }
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    res = runner.invoke(
        app,
        ["issue", "correlate", "--path", str(medium_repo), "--issue", str(issue_file)],
    )
    assert res.exit_code == 0, res.stdout
    assert "Primary anchor" in res.stdout
    assert "pkg.services.handle_request" in res.stdout or "pkg/services.py" in res.stdout


def test_issue_correlate_command_invalid_json(medium_repo: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    runner = CliRunner()
    res = runner.invoke(
        app, ["issue", "correlate", "--path", str(medium_repo), "--issue", str(bad)]
    )
    assert res.exit_code != 0
