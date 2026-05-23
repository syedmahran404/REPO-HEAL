"""In-process BM25 over chunked source.

Implements the Okapi BM25 weighting (k1 and b configurable). Pure
Python, dependency-free. Performance is acceptable for repos up to
~50k chunks. Beyond that we swap in an inverted-index backend behind
the same Protocol.

We keep a small inverted index (term → list[(doc_idx, tf)]) so query
scoring scales with the number of distinct query terms rather than
the number of documents. Without it, scoring is O(N) per term per
query — fine for tests but not for production.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from typing import Iterable


class BM25Index:
    """An indexed, queryable BM25 corpus.

    Construct once from a list of pre-tokenized documents; query many
    times. Re-tokenization is the caller's responsibility (the same
    tokenizer must be used for the corpus and the query — see
    :mod:`repoheal.retrieval.tokenize`).
    """

    def __init__(
        self,
        docs: Sequence[Sequence[str]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._k1 = k1
        self._b = b
        self._n_docs = len(docs)
        self._doc_lens: list[int] = [len(d) for d in docs]
        self._avgdl: float = (sum(self._doc_lens) / self._n_docs) if self._n_docs else 0.0

        # tf[i] = Counter(term -> count) for doc i
        self._tf: list[Counter[str]] = [Counter(d) for d in docs]

        # Inverted index: term -> list of doc indices that contain it.
        self._postings: dict[str, list[int]] = {}
        for i, counts in enumerate(self._tf):
            for term in counts:
                self._postings.setdefault(term, []).append(i)

    # ------------------------------------------------------------------

    @property
    def n_docs(self) -> int:
        return self._n_docs

    @property
    def avgdl(self) -> float:
        return self._avgdl

    def idf(self, term: str) -> float:
        """Smoothed IDF (the +1 prevents negative scores when most docs
        contain a term)."""
        df = len(self._postings.get(term, ()))
        return math.log((self._n_docs - df + 0.5) / (df + 0.5) + 1.0)

    # ------------------------------------------------------------------

    def search(
        self,
        query_tokens: Iterable[str],
        *,
        top_k: int = 20,
    ) -> list[tuple[int, float]]:
        """Return ``[(doc_index, score), ...]`` sorted by score desc."""
        scores: dict[int, float] = {}
        # We iterate distinct terms only; duplicate query terms add the
        # same score per doc and don't change ranking, so dedup is fine
        # and faster.
        seen_terms: set[str] = set()
        for term in query_tokens:
            if term in seen_terms:
                continue
            seen_terms.add(term)
            posting = self._postings.get(term)
            if not posting:
                continue
            idf = self.idf(term)
            k1 = self._k1
            b = self._b
            avgdl = self._avgdl or 1.0
            for doc_idx in posting:
                f = self._tf[doc_idx][term]
                dl = self._doc_lens[doc_idx]
                denom = f + k1 * (1 - b + b * dl / avgdl)
                if denom == 0:
                    continue
                contribution = idf * (f * (k1 + 1)) / denom
                scores[doc_idx] = scores.get(doc_idx, 0.0) + contribution

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_k]


__all__ = ["BM25Index"]
