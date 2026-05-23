"""Tests for the patch ranking engine."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from repoheal.core.models import (
    Ecosystem,
    FileEdit,
    Patch,
    Repository,
    ValidationReport,
    ValidationResult,
    Verdict,
)
from repoheal.patching import (
    DiffSizeScorer,
    GraphImpactScorer,
    PatchCandidate,
    PatchRanker,
    RankingContext,
    StyleConsistencyScorer,
    ValidationScorer,
    default_scorers,
)


# --- helpers -------------------------------------------------------------


@pytest.fixture
def working_repo(tmp_path: Path, tiny_repo: Path) -> Repository:
    dst = tmp_path / "tiny_repo"
    shutil.copytree(tiny_repo, dst)
    return Repository(name="tiny_repo", root=dst, ecosystem=Ecosystem(), files=[])


def _patch(*, file: str, content: str) -> Patch:
    return Patch(
        title="t",
        description="d",
        edits=(FileEdit(file=Path(file), new_content=content),),
    )


def _report(verdict: Verdict) -> ValidationReport:
    return ValidationReport(
        overall=verdict,
        results=(ValidationResult(validator="x", verdict=verdict, summary="ok"),),
    )


# --- ValidationScorer ---------------------------------------------------


def test_validation_scorer_pass_is_one(working_repo: Repository) -> None:
    cand = PatchCandidate(_patch(file="x.py", content="x = 1\n"), validation=_report(Verdict.PASS))
    s = ValidationScorer().score(cand, RankingContext(repo=working_repo))
    assert s == 1.0


def test_validation_scorer_warn_is_half(working_repo: Repository) -> None:
    cand = PatchCandidate(_patch(file="x.py", content="x = 1\n"), validation=_report(Verdict.WARN))
    assert ValidationScorer().score(cand, RankingContext(repo=working_repo)) == 0.5


def test_validation_scorer_fail_is_zero(working_repo: Repository) -> None:
    cand = PatchCandidate(_patch(file="x.py", content="x = 1\n"), validation=_report(Verdict.FAIL))
    assert ValidationScorer().score(cand, RankingContext(repo=working_repo)) == 0.0


def test_validation_scorer_unknown_is_neutral(working_repo: Repository) -> None:
    cand = PatchCandidate(_patch(file="x.py", content="x = 1\n"))  # no report
    assert ValidationScorer().score(cand, RankingContext(repo=working_repo)) == 0.5


# --- DiffSizeScorer -----------------------------------------------------


def test_diff_size_smaller_scores_higher(working_repo: Repository) -> None:
    # Small diff: change one variable assignment in pkg/a.py.
    target = working_repo.root / "pkg" / "a.py"
    original = target.read_text()
    small = original.replace('CONSTANT_A = "a"', 'CONSTANT_A = "b"')
    big = original + "\n" + ("# pad\n" * 200)

    small_cand = PatchCandidate(_patch(file="pkg/a.py", content=small))
    big_cand = PatchCandidate(_patch(file="pkg/a.py", content=big))

    scorer = DiffSizeScorer(half_life_lines=20)
    ctx = RankingContext(repo=working_repo)
    assert scorer.score(small_cand, ctx) > scorer.score(big_cand, ctx)


def test_diff_size_zero_diff_is_one(working_repo: Repository) -> None:
    target = working_repo.root / "pkg" / "a.py"
    no_change = PatchCandidate(_patch(file="pkg/a.py", content=target.read_text()))
    scorer = DiffSizeScorer()
    assert scorer.score(no_change, RankingContext(repo=working_repo)) == 1.0


def test_diff_size_validates_construction() -> None:
    with pytest.raises(ValueError):
        DiffSizeScorer(half_life_lines=0)


# --- GraphImpactScorer --------------------------------------------------


def test_graph_impact_no_graph_is_neutral(working_repo: Repository) -> None:
    cand = PatchCandidate(_patch(file="pkg/a.py", content="x = 1\n"))
    s = GraphImpactScorer().score(cand, RankingContext(repo=working_repo, graph=None))
    assert s == 0.5


def test_graph_impact_validates_construction() -> None:
    with pytest.raises(ValueError):
        GraphImpactScorer(half_life_consumers=0)


# --- StyleConsistencyScorer ---------------------------------------------


def test_style_perfect_match_scores_one(working_repo: Repository, tmp_path: Path) -> None:
    f = working_repo.root / "style.py"
    f.write_text('def x():\n    return "a"\n', encoding="utf-8")

    matching = PatchCandidate(_patch(file="style.py", content='def y():\n    return "b"\n'))
    s = StyleConsistencyScorer().score(matching, RankingContext(repo=working_repo))
    assert s == 1.0


def test_style_quote_mismatch_scores_below_one(working_repo: Repository) -> None:
    f = working_repo.root / "style.py"
    f.write_text('x = "a"\ny = "b"\n', encoding="utf-8")  # double-quote dominant

    mismatch = PatchCandidate(_patch(file="style.py", content="x = 'a'\ny = 'b'\nz = 'c'\n"))
    s = StyleConsistencyScorer().score(mismatch, RankingContext(repo=working_repo))
    assert s < 1.0


def test_style_eol_mismatch_scores_below_one(working_repo: Repository) -> None:
    f = working_repo.root / "style.py"
    f.write_bytes(b"x = 1\r\ny = 2\r\n")  # CRLF original

    mismatch = PatchCandidate(_patch(file="style.py", content="x = 1\ny = 2\n"))  # LF
    s = StyleConsistencyScorer().score(mismatch, RankingContext(repo=working_repo))
    assert s < 1.0


def test_style_new_files_neutral(working_repo: Repository) -> None:
    edit = FileEdit(file=Path("brand_new.py"), new_content="x = 1\n", is_new_file=True)
    cand = PatchCandidate(Patch(title="t", description="d", edits=(edit,)))
    s = StyleConsistencyScorer().score(cand, RankingContext(repo=working_repo))
    assert s == 1.0


# --- PatchRanker --------------------------------------------------------


def test_ranker_orders_by_composite_desc(working_repo: Repository) -> None:
    """The pass-validation candidate must rank above the fail candidate."""
    target = working_repo.root / "pkg" / "a.py"
    base = target.read_text()
    new_content = base.replace('CONSTANT_A = "a"', 'CONSTANT_A = "x"')

    pass_cand = PatchCandidate(_patch(file="pkg/a.py", content=new_content), validation=_report(Verdict.PASS))
    fail_cand = PatchCandidate(_patch(file="pkg/a.py", content=new_content), validation=_report(Verdict.FAIL))

    ranker = PatchRanker()
    ranked = ranker.rank([fail_cand, pass_cand], RankingContext(repo=working_repo))
    # First should be PASS.
    assert ranked[0][0] is pass_cand
    assert ranked[1][0] is fail_cand
    # PASS composite should be strictly higher.
    assert ranked[0][1].composite > ranked[1][1].composite


def test_ranker_breakdown_shows_per_scorer_contributions(working_repo: Repository) -> None:
    cand = PatchCandidate(
        _patch(file="pkg/a.py", content="x = 1\n"),
        validation=_report(Verdict.PASS),
    )
    ranker = PatchRanker()
    ranked = ranker.rank([cand], RankingContext(repo=working_repo))
    breakdown = ranked[0][1].breakdown
    assert "validation" in breakdown
    assert "diff_size" in breakdown
    assert "graph_impact" in breakdown
    assert "style_consistency" in breakdown
    # Validation-pass = 1.0.
    assert breakdown["validation"] == 1.0


def test_ranker_composite_in_unit_interval(working_repo: Repository) -> None:
    cand = PatchCandidate(
        _patch(file="pkg/a.py", content="x = 1\n"),
        validation=_report(Verdict.PASS),
    )
    ranker = PatchRanker()
    score = ranker.rank([cand], RankingContext(repo=working_repo))[0][1].composite
    assert 0.0 <= score <= 1.0


def test_ranker_handles_empty_candidate_list(working_repo: Repository) -> None:
    ranker = PatchRanker()
    assert ranker.rank([], RankingContext(repo=working_repo)) == []


def test_ranker_validates_weights() -> None:
    with pytest.raises(ValueError):
        PatchRanker(scorers=[])
    with pytest.raises(ValueError):
        PatchRanker(scorers=[(ValidationScorer(), 0.0)])
    with pytest.raises(ValueError):
        PatchRanker(scorers=[(ValidationScorer(), -1.0)])


def test_ranker_swallows_individual_scorer_crashes(working_repo: Repository) -> None:
    """A misbehaving scorer must not blow up the entire ranking."""

    class _Bomb:
        @property
        def name(self) -> str:
            return "bomb"

        def score(self, candidate, ctx) -> float:
            raise RuntimeError("oh no")

    cand = PatchCandidate(_patch(file="x.py", content="x = 1\n"), validation=_report(Verdict.PASS))
    ranker = PatchRanker(scorers=[(ValidationScorer(), 1.0), (_Bomb(), 1.0)])
    ranked = ranker.rank([cand], RankingContext(repo=working_repo))
    score = ranked[0][1]
    # Bomb's contribution is 0; validation 1; weighted average = 0.5.
    assert score.breakdown["bomb"] == 0.0
    assert score.breakdown["validation"] == 1.0
    assert score.composite == 0.5


def test_default_scorers_are_present() -> None:
    names = [s.name for s, _ in default_scorers()]
    assert names == ["validation", "diff_size", "graph_impact", "style_consistency"]
