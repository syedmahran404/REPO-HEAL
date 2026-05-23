"""Reciprocal Rank Fusion.

Combines multiple ranked lists without weight tuning. The score for a
document is ``Σ 1 / (k + rank_i)`` where ``rank_i`` is its 1-indexed
rank in the i-th list (∞ if absent). It is provably robust when the
component rankers are at least somewhat independent.

ADR-0003 picks RRF over weighted-sum-of-normalised-scores precisely so
we never have to retune when an embedding model changes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def rrf_fuse(
    rankings: Iterable[Sequence[str]],
    *,
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse ranked lists.

    Parameters
    ----------
    rankings:
        An iterable of ranked lists of document ids. Best-ranked first.
    k:
        Smoothing constant. The standard 60 from the original RRF paper
        is a fine default; smaller k makes top results dominate, larger
        k spreads influence further down each list.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


__all__ = ["rrf_fuse"]
