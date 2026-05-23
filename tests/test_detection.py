"""Tests for the detection subsystem."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoheal.analysis import AnalysisService
from repoheal.detection import CircularImportRule, RuleRegistry
from repoheal.exceptions import DetectionError

pytest.importorskip("tree_sitter_languages")


def test_circular_import_rule_finds_pkg_a_b_cycle(tiny_repo: Path) -> None:
    result = AnalysisService().analyze(str(tiny_repo))
    matching = [f for f in result.findings if f.rule_id == "circular_imports"]
    assert matching, "expected at least one circular_imports finding"
    # Every reported cycle must include both pkg.a and pkg.b.
    found = False
    for f in matching:
        modules = f.metadata.get("modules", [])
        if "pkg.a" in modules and "pkg.b" in modules:
            found = True
            break
    assert found, f"pkg.a / pkg.b cycle not present in findings: {matching}"


def test_circular_import_rule_skips_external_only_cycles(tiny_repo: Path) -> None:
    # pkg.standalone imports stdlib `json`. That import touches an
    # external module node; the rule must NOT report a finding for
    # that, since there's no internal cycle there.
    result = AnalysisService().analyze(str(tiny_repo))
    for f in result.findings:
        if f.rule_id == "circular_imports":
            mods = f.metadata.get("modules", [])
            assert "json" not in mods


def test_rule_registry_filters_by_id(tiny_repo: Path) -> None:
    result = AnalysisService().analyze(str(tiny_repo), rule_ids=["circular_imports"])
    assert all(f.rule_id == "circular_imports" for f in result.findings)


def test_rule_registry_unknown_id_raises(tiny_repo: Path) -> None:
    with pytest.raises(DetectionError):
        AnalysisService().analyze(str(tiny_repo), rule_ids=["does_not_exist"])


def test_rule_registry_register_rejects_duplicate() -> None:
    reg = RuleRegistry([CircularImportRule()])
    with pytest.raises(ValueError):
        reg.register(CircularImportRule())
