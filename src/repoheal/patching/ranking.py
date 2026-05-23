"""Patch ranking — score and order candidate fixes.

When the agent loop produces N candidate patches for the same finding,
we need a defensible way to pick the best one. This module gives us a
**composable** scorer pipeline: each scorer maps ``(candidate,
context) -> float`` in [0, 1], and the ranker fuses with caller-
specified weights.

Default scorers:

* :class:`ValidationScorer`       — verdict from the validation pipeline.
* :class:`DiffSizeScorer`         — smaller diffs preferred.
* :class:`GraphImpactScorer`      — fewer downstream consumers preferred.
* :class:`StyleConsistencyScorer` — match the file's existing style.

The ranker outputs a :class:`RankingScore` per candidate with the
composite score *and the per-component breakdown*. The breakdown is
the explainability story: every score traces back to an interpretable
component, so a reviewer can ask "why did patch X win?" and get an
answer that does not hand-wave.

There is no hidden weighting magic. Default weights live in
:func:`default_scorers` and can be overridden at construction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..core.models import Patch, Repository, ValidationReport, Verdict
from ..core.protocols import GraphBackend
from ..graph.queries import ImpactAnalysis
from ..graph.schema import EdgeKind, module_node_id
from ..logging import get_logger
from .diff import UnifiedDiffGenerator

_log = get_logger(__name__)


# =============================================================================
# Data shapes
# =============================================================================


@dataclass(frozen=True)
class PatchCandidate:
    """One candidate fix going into the ranker.

    The validation report is optional but expected: the agent loop runs
    :class:`ValidationPipeline` on every candidate before ranking. A
    missing report yields a "neutral" validation score (0.5)."""

    patch: Patch
    validation: ValidationReport | None = None


@dataclass(frozen=True)
class RankingContext:
    """Inputs every scorer can read.

    Kept dataclass-shallow so adding a field doesn't break Protocol
    compliance for existing scorers."""

    repo: Repository
    graph: GraphBackend | None = None


class RankingScore(BaseModel):
    """The ranker's verdict on one candidate.

    ``composite`` is the weighted sum, normalised so a perfect score is
    1.0 and a zero score is 0.0. ``breakdown`` records each scorer's
    raw contribution — the *explanation*, not the *weighted* number, so
    the user can see which signals drove the ranking."""

    model_config = ConfigDict(frozen=True)

    patch_id: UUID
    composite: float
    breakdown: dict[str, float] = Field(default_factory=dict)


# =============================================================================
# Scorer Protocol
# =============================================================================


@runtime_checkable
class Scorer(Protocol):
    """A single named function ``(candidate, ctx) -> [0, 1]``.

    Implementations are stateless or close to it. Scorers must not
    mutate the working tree, the patch, or the graph."""

    @property
    def name(self) -> str: ...

    def score(self, candidate: PatchCandidate, ctx: RankingContext) -> float: ...


# =============================================================================
# Concrete scorers
# =============================================================================


class ValidationScorer:
    """Map the validation pipeline's verdict to a [0, 1] score."""

    @property
    def name(self) -> str:
        return "validation"

    def score(self, candidate: PatchCandidate, ctx: RankingContext) -> float:
        if candidate.validation is None:
            return 0.5  # unknown

        verdict_to_score = {
            Verdict.PASS: 1.0,
            Verdict.WARN: 0.5,
            Verdict.FAIL: 0.0,
            Verdict.ERROR: 0.0,
        }
        return verdict_to_score.get(candidate.validation.overall, 0.0)


class DiffSizeScorer:
    """Smaller diffs preferred. ``1 / (1 + total / half_life)``.

    With default ``half_life_lines=50``, a 50-line diff scores 0.5 and
    a 200-line diff scores 0.2. The decay is gentle on purpose: we
    don't want to crush a 100-line *correct* fix in favour of a 5-line
    incorrect one.

    Computes diff statistics by re-running ``UnifiedDiffGenerator``
    against the on-disk state. Caller must rank *before* applying any
    candidate, otherwise the diff against disk is wrong.
    """

    def __init__(self, *, half_life_lines: int = 50) -> None:
        if half_life_lines <= 0:
            raise ValueError("half_life_lines must be > 0")
        self._half = float(half_life_lines)
        self._diff_gen = UnifiedDiffGenerator()

    @property
    def name(self) -> str:
        return "diff_size"

    def score(self, candidate: PatchCandidate, ctx: RankingContext) -> float:
        try:
            diff = self._diff_gen.generate(ctx.repo, candidate.patch)
        except Exception as exc:  # diff generation failure shouldn't fail the rank
            _log.warning(
                "ranking.diff_size_failed",
                patch_id=str(candidate.patch.id),
                error=str(exc),
            )
            return 0.5

        added = removed = 0
        for line in diff.splitlines():
            if line.startswith(("+++", "---")) or line.startswith("@@"):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1

        total = added + removed
        return 1.0 / (1.0 + total / self._half)


class GraphImpactScorer:
    """Fewer transitive consumers preferred.

    For each file the patch touches, we look up the corresponding
    module node in the graph and count its incoming ``IMPORTS`` and
    ``REFERENCES`` edges (deduplicated across files). Patches that
    touch widely-imported modules score lower; patches that touch
    leaf modules score near 1.0.

    No graph available → neutral 0.5.
    """

    def __init__(self, *, half_life_consumers: int = 5) -> None:
        if half_life_consumers <= 0:
            raise ValueError("half_life_consumers must be > 0")
        self._half = float(half_life_consumers)

    @property
    def name(self) -> str:
        return "graph_impact"

    def score(self, candidate: PatchCandidate, ctx: RankingContext) -> float:
        if ctx.graph is None:
            return 0.5

        consumers: set[str] = set()
        for edit in candidate.patch.edits:
            if edit.is_deletion or edit.is_new_file:
                continue
            module_qname = _module_qname_from_path(edit.file)
            module_id = module_node_id(module_qname)
            if not ctx.graph.has_node(module_id):
                continue
            for kind in (EdgeKind.IMPORTS.value, EdgeKind.REFERENCES.value):
                try:
                    consumers.update(
                        ctx.graph.neighbors(module_id, kind=kind, direction="in")
                    )
                except Exception:
                    continue

        return 1.0 / (1.0 + len(consumers) / self._half)


class StyleConsistencyScorer:
    """Heuristic style match between the new content and the existing file.

    Three sub-checks, averaged:

    * **Indentation.** Tabs-vs-spaces match.
    * **Quote style.** Dominant single/double-quote preference match.
    * **Line endings.** CRLF-vs-LF match.

    Each sub-check returns 1.0 (match), 0.5 (ambiguous), or 0.0
    (mismatch). New files and deletions contribute neutrally.

    This is **not** a replacement for ``ruff format`` / ``black`` — those
    live in the validation pipeline. Style consistency is one tie-breaker
    among many."""

    @property
    def name(self) -> str:
        return "style_consistency"

    def score(self, candidate: PatchCandidate, ctx: RankingContext) -> float:
        sub_scores: list[float] = []
        for edit in candidate.patch.edits:
            if edit.is_deletion or edit.is_new_file:
                continue
            target = ctx.repo.root / edit.file
            if not target.exists():
                continue
            try:
                old = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            new = edit.new_content
            sub_scores.append(_consistency(old, new))

        if not sub_scores:
            return 1.0  # nothing to compare → neutral pass
        return sum(sub_scores) / len(sub_scores)


# =============================================================================
# PatchRanker
# =============================================================================


class PatchRanker:
    """Compose scorers, rank candidates."""

    def __init__(
        self,
        scorers: Sequence[tuple[Scorer, float]] | None = None,
    ) -> None:
        if scorers is None:
            self._scorers = list(default_scorers())
        else:
            self._scorers = list(scorers)
            if not self._scorers:
                raise ValueError("at least one scorer required")
            for _, w in self._scorers:
                if w < 0:
                    raise ValueError("weights must be non-negative")
            if sum(w for _, w in self._scorers) <= 0:
                raise ValueError("scorer weights must sum to > 0")

    # ------------------------------------------------------------------

    def rank(
        self,
        candidates: Sequence[PatchCandidate],
        ctx: RankingContext,
    ) -> list[tuple[PatchCandidate, RankingScore]]:
        """Return ``(candidate, score)`` pairs sorted by composite desc.

        Stable on ties: the input order breaks ties."""
        scored: list[tuple[PatchCandidate, RankingScore]] = []
        for cand in candidates:
            scored.append((cand, self._score_one(cand, ctx)))

        scored.sort(key=lambda pair: pair[1].composite, reverse=True)
        return scored

    # ------------------------------------------------------------------

    def _score_one(
        self,
        candidate: PatchCandidate,
        ctx: RankingContext,
    ) -> RankingScore:
        breakdown: dict[str, float] = {}
        weighted_sum = 0.0
        total_weight = 0.0
        for scorer, weight in self._scorers:
            try:
                raw = float(scorer.score(candidate, ctx))
            except Exception as exc:
                _log.warning(
                    "ranking.scorer_crashed",
                    scorer=scorer.name,
                    patch_id=str(candidate.patch.id),
                    error=str(exc),
                )
                raw = 0.0
            raw = max(0.0, min(1.0, raw))
            breakdown[scorer.name] = round(raw, 4)
            weighted_sum += raw * weight
            total_weight += weight

        composite = weighted_sum / total_weight if total_weight > 0 else 0.0
        return RankingScore(
            patch_id=candidate.patch.id,
            composite=round(composite, 4),
            breakdown=breakdown,
        )


def default_scorers() -> Sequence[tuple[Scorer, float]]:
    """Default weights.

    Validation dominates — a failing patch is *never* the best choice.
    Diff size and graph impact are co-equal. Style is a tie-breaker."""
    return (
        (ValidationScorer(), 4.0),
        (DiffSizeScorer(), 1.0),
        (GraphImpactScorer(), 1.0),
        (StyleConsistencyScorer(), 0.5),
    )


# =============================================================================
# Helpers
# =============================================================================


def _module_qname_from_path(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else path.stem


def _consistency(old: str, new: str) -> float:
    sub = [_indent_match(old, new), _quote_match(old, new), _eol_match(old, new)]
    return sum(sub) / len(sub)


def _uses_tabs(text: str) -> bool:
    tab_lines = sum(1 for line in text.splitlines() if line.startswith("\t"))
    space_lines = sum(
        1 for line in text.splitlines() if line.startswith(" ") and not line.startswith("\t")
    )
    return tab_lines > space_lines


def _indent_match(old: str, new: str) -> float:
    return 1.0 if _uses_tabs(old) == _uses_tabs(new) else 0.0


def _quote_match(old: str, new: str) -> float:
    old_score = _quote_preference(old)
    new_score = _quote_preference(new)
    if old_score is None or new_score is None:
        return 1.0  # nothing to compare; don't penalise
    return 1.0 if old_score == new_score else 0.5


def _quote_preference(text: str) -> str | None:
    """``"double"`` or ``"single"`` or ``None`` if there are no quotes."""
    d = text.count('"')
    s = text.count("'")
    if d == 0 and s == 0:
        return None
    return "double" if d >= s else "single"


def _eol_match(old: str, new: str) -> float:
    return 1.0 if ("\r\n" in old) == ("\r\n" in new) else 0.0


__all__ = [
    "DiffSizeScorer",
    "GraphImpactScorer",
    "PatchCandidate",
    "PatchRanker",
    "RankingContext",
    "RankingScore",
    "Scorer",
    "StyleConsistencyScorer",
    "ValidationScorer",
    "default_scorers",
]
