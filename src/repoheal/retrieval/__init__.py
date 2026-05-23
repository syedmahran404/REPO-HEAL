"""Retrieval subsystem — INTERFACE-DEFINED ONLY in Phase 1.

The architecture is locked in :doc:`docs/decisions/ADR-0003-retrieval-architecture`:
three-stage hybrid (BM25 + dense + graph expansion) with RRF fusion and
optional cross-encoder reranking.

What ships now:

* :class:`EmbeddingProvider` Protocol.
* :class:`VectorStore` Protocol.
* :class:`Reranker` Protocol.
* :class:`Chunker` Protocol.
* :class:`HybridRetriever` Protocol.

What does **not** ship: any concrete implementation. Implementations
are intentionally deferred until we pick an embedding provider; doing
otherwise would lock in the wrong ranking signals.

See :doc:`docs/ROADMAP.md` for the next steps.
"""

from .protocols import (
    Chunker,
    EmbeddingProvider,
    HybridRetriever,
    Reranker,
    VectorStore,
)

__all__ = [
    "Chunker",
    "EmbeddingProvider",
    "HybridRetriever",
    "Reranker",
    "VectorStore",
]
