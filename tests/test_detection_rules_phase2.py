"""Tests for the seven Phase 2 detection rules.

Each rule is exercised against the medium_repo fixture, which has
deliberate positive cases for every rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repoheal.analysis import AnalysisService
from repoheal.detection import (
    BroadExceptRule,
    DeadCodeRule,
    GodClassRule,
    HardcodedSecretRule,
    LongMethodRule,
    MutableDefaultArgsRule,
    UnusedImportRule,
)
from repoheal.detection.registry import RuleRegistry

pytest.importorskip("tree_sitter_languages")


def _findings_for(path: Path, rule_id: str) -> list:
    """Run only one rule against a path and return its findings."""
    result = AnalysisService().analyze(str(path), rule_ids=[rule_id])
    return [f for f in result.findings if f.rule_id == rule_id]


# --- unused_imports -------------------------------------------------------


def test_unused_imports_finds_unused_os_in_utils(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "unused_imports")
    bound = {(str(f.file), f.metadata["bound_name"]) for f in findings}
    assert any("utils.py" in path and name == "os" for path, name in bound)


def test_unused_imports_skips_used_imports(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "unused_imports")
    # ``Callable`` IS used in utils.py (parameter type) — must NOT be flagged.
    bound = {(str(f.file), f.metadata["bound_name"]) for f in findings}
    assert all(name != "Callable" for _, name in bound)


def test_unused_imports_emits_confidence(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "unused_imports")
    assert findings, "expected at least one unused-imports finding"
    for f in findings:
        assert 0.0 <= f.metadata.get("confidence", -1) <= 1.0


# --- mutable_default_args -------------------------------------------------


def test_mutable_default_args_finds_list_default(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "mutable_default_args")
    fns = {f.metadata["function"] for f in findings}
    assert "with_mutable_default" in fns


def test_mutable_default_args_confidence_is_one(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "mutable_default_args")
    for f in findings:
        assert f.metadata["confidence"] == 1.0


# --- broad_except --------------------------------------------------------


def test_broad_except_finds_except_exception(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "broad_except")
    assert any("utils.py" in str(f.file) for f in findings)


def test_broad_except_does_not_flag_specific(tmp_path: Path) -> None:
    # Build a tiny throwaway repo with a SPECIFIC except.
    src = tmp_path / "repo"
    src.mkdir()
    (src / "x.py").write_text(
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError:\n"
        "        return None\n",
        encoding="utf-8",
    )
    (src / "__init__.py").write_text("")
    findings = _findings_for(src, "broad_except")
    assert findings == []


def test_broad_except_does_not_flag_handler_that_reraises(tmp_path: Path) -> None:
    src = tmp_path / "repo"
    src.mkdir()
    (src / "x.py").write_text(
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        raise RuntimeError('bad')\n",
        encoding="utf-8",
    )
    (src / "__init__.py").write_text("")
    findings = _findings_for(src, "broad_except")
    assert findings == []


# --- dead_code -----------------------------------------------------------


def test_dead_code_finds_private_unused_function(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "dead_code")
    qnames = {f.metadata["qualified_name"] for f in findings}
    assert "pkg.services._helper_unused" in qnames


def test_dead_code_does_not_flag_public_or_dunder(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "dead_code")
    qnames = {f.metadata["qualified_name"] for f in findings}
    # Public functions like handle_request must NOT be flagged even
    # though they have few callers.
    assert "pkg.services.handle_request" not in qnames
    # Dunder __init__ must NOT be flagged.
    assert all(not q.endswith(".__init__") for q in qnames)


# --- long_method ---------------------------------------------------------


def test_long_method_finds_overlong_function(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "long_method")
    qnames = {f.metadata["qualified_name"] for f in findings}
    assert "pkg.services.long_method" in qnames


def test_long_method_threshold_configurable() -> None:
    # We don't run the rule end-to-end here; just sanity-check the
    # constructor exposes the threshold.
    rule = LongMethodRule(line_threshold=5)
    assert rule._threshold == 5  # type: ignore[attr-defined]


# --- god_class -----------------------------------------------------------


def test_god_class_finds_too_many_methods(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "god_class")
    qnames = {f.metadata["qualified_name"] for f in findings}
    assert "pkg.models.GodClass" in qnames


def test_god_class_does_not_flag_normal_class(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "god_class")
    qnames = {f.metadata["qualified_name"] for f in findings}
    # User has 2 methods; must not be flagged.
    assert "pkg.models.User" not in qnames
    assert "pkg.models.Admin" not in qnames


# --- hardcoded_secret ----------------------------------------------------


def test_hardcoded_secret_finds_aws_pattern(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "hardcoded_secret")
    labels = [f.metadata.get("label") for f in findings]
    assert "aws_access_key_id" in labels


def test_stripe_regex_matches_runtime_assembled_value() -> None:
    """The Stripe pattern is exercised here without ever putting a
    literal Stripe-shaped key in source — GitHub Push Protection
    flags such literals even when synthetic, so we assemble the
    test value at runtime."""
    import re

    from repoheal.detection.rules.python_rules import _SECRET_PATTERNS

    stripe_entry = next(
        (p, label) for p, label, _ in _SECRET_PATTERNS if label == "stripe_live_key"
    )
    pattern, label = stripe_entry
    # Assemble at runtime so the literal never appears in source.
    test_value = "sk_" + "live_" + ("x" * 24)
    assert re.match(pattern, test_value) is not None
    assert label == "stripe_live_key"


def test_hardcoded_secret_finds_high_entropy_url(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "hardcoded_secret")
    names = {f.metadata.get("name") for f in findings}
    # API_TOKEN matches the suspicious-name heuristic + entropy threshold.
    assert "API_TOKEN" in names


def test_hardcoded_secret_never_echoes_value(medium_repo: Path) -> None:
    findings = _findings_for(medium_repo, "hardcoded_secret")
    for f in findings:
        # The secret value must not appear verbatim in the description.
        # The fixture's AWS key is the documented EXAMPLE one — its
        # presence in a description would still be a defect because
        # the *general* property is "rule never echoes the value".
        assert "AKIAIOSFODNN7EXAMPLE" not in f.description


# --- registry default ----------------------------------------------------


def test_default_registry_includes_all_phase2_rules() -> None:
    from repoheal.detection import default_registry

    ids = set(default_registry().ids())
    expected = {
        "circular_imports",
        "unused_imports",
        "mutable_default_args",
        "broad_except",
        "dead_code",
        "long_method",
        "god_class",
        "hardcoded_secret",
    }
    assert expected.issubset(ids)


def test_individual_rule_classes_construct_without_args() -> None:
    # Smoke test: every Phase 2 rule must be default-constructible
    # so the registry can wire them up.
    for cls in (
        UnusedImportRule,
        MutableDefaultArgsRule,
        BroadExceptRule,
        DeadCodeRule,
        LongMethodRule,
        GodClassRule,
        HardcodedSecretRule,
    ):
        rule = cls()
        assert rule.rule_id
        assert rule.title


def test_rules_register_with_registry() -> None:
    reg = RuleRegistry([UnusedImportRule(), BroadExceptRule()])
    assert sorted(reg.ids()) == ["broad_except", "unused_imports"]
