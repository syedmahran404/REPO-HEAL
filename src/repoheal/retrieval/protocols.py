"""Retrieval Protocols.

These define the seams of the hybrid retrieval pipeline. Concrete
implementations live in their own modules (``chunking``, ``bm25``,
``vector_store``, ``embeddings``, ``fusion``, ``graph_expansion``,
``packing``, ``service``) so each can be swapped independently.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

from ..core.models import Chunk, FileRef


@runtime_checkable
class Chunker(Protocol):
    """Slice a source file into retrievable chunks.

    Implementations should respect symbol boundaries: never split a
    function across chunks. The graph already knows where the
    boundaries are.
    """

    def chunk(self, file: FileRef, source: bytes) -> Sequence[Chunk]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Produce vector embeddings for chunks/queries."""

    @property
    def dimension(self) -> int: ...

    def embed_chunks(self, texts: Iterable[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class VectorStore(Protocol):
    """Store and retrieve dense vectors."""

    def upsert(self, ids: Sequence[str], vectors: Sequence[Sequence[float]]) -> None: ...

    def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int = 20,
    ) -> list[tuple[str, float]]: ...

    def delete(self, ids: Sequence[str]) -> None: ...

    def __len__(self) -> int: ...


@runtime_checkable
class Reranker(Protocol):
    """Cross-encoder rerank step (optional Stage 4)."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[tuple[str, str]],
    ) -> list[tuple[str, float]]: ...


@runtime_checkable
class HybridRetriever(Protocol):
    """The full pipeline: BM25 + vector + graph + (optional) rerank."""

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 10,
        token_budget: int | None = None,
    ) -> list[Chunk]: ...
