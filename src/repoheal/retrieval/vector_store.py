"""In-memory vector store.

Brute-force cosine search over a list of (id, vector) pairs. Adequate
up to ~50k chunks on a single machine; swap to FAISS or Qdrant via the
:class:`~repoheal.retrieval.protocols.VectorStore` Protocol when we
outgrow it.

We store vectors as plain ``list[float]``. Numpy would be faster but
introduces an optional dependency at the level of the *core* retrieval
pipeline, which we want to keep dep-free. The hot path is tens of
microseconds per query at the target scale; if profiling says
otherwise, FAISS is the move.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


class InMemoryVectorStore:
    """Concrete :class:`VectorStore` backed by a plain Python list."""

    def __init__(self) -> None:
        # Parallel arrays: same index in both.
        self._ids: list[str] = []
        self._vectors: list[list[float]] = []
        # id -> position, for O(1) upsert / delete.
        self._index: dict[str, int] = {}

    # ------------------------------------------------------------------

    def upsert(self, ids: Sequence[str], vectors: Sequence[Sequence[float]]) -> None:
        if len(ids) != len(vectors):
            raise ValueError(
                f"ids and vectors must be the same length: {len(ids)} != {len(vectors)}"
            )
        for vid, vec in zip(ids, vectors, strict=True):
            v = list(vec)
            existing = self._index.get(vid)
            if existing is not None:
                self._vectors[existing] = v
            else:
                self._index[vid] = len(self._ids)
                self._ids.append(vid)
                self._vectors.append(v)

    def delete(self, ids: Sequence[str]) -> None:
        for vid in ids:
            pos = self._index.pop(vid, None)
            if pos is None:
                continue
            # Swap-remove for O(1) deletion.
            last = len(self._ids) - 1
            if pos != last:
                self._ids[pos] = self._ids[last]
                self._vectors[pos] = self._vectors[last]
                self._index[self._ids[pos]] = pos
            self._ids.pop()
            self._vectors.pop()

    def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int = 20,
    ) -> list[tuple[str, float]]:
        if not self._ids:
            return []
        # Normalise the query so all comparisons are cosine.
        query = _l2_normalise(list(vector))
        scored: list[tuple[str, float]] = []
        for vid, v in zip(self._ids, self._vectors, strict=False):
            scored.append((vid, _cosine_normalised(query, v)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def __len__(self) -> int:
        return len(self._ids)


# --- helpers ----------------------------------------------------------


def _l2_normalise(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0.0:
        return v
    return [x / norm for x in v]


def _cosine_normalised(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity assuming inputs are already L2-normalised.

    Falls back to a true cosine computation if ``b`` is not normalised
    (cheap defensive check)."""
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        nb += y * y
    if nb == 0.0:
        return 0.0
    # If b is normalised, nb≈1 and we just return dot.
    if abs(nb - 1.0) < 1e-6:
        return dot
    return dot / math.sqrt(nb)


__all__ = ["InMemoryVectorStore"]
