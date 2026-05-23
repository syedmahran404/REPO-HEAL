"""Retrieval Protocols.

These are the seams; the concrete code is the next milestone.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

from ..core.models import FileRef


class Chunk(Protocol):
    """A retrievable unit of source content."""

    @property
    def id(self) -> str: ...
    @property
    def file(self) -> FileRef: ...
    @property
    def text(self) -> str: ...
    @property
    def start_line(self) -> int: ...
    @property
    def end_line(self) -> int: ...


@runtime_checkable
class Chunker(Protocol):
    """Slice a source file into retrievable chunks.

    Implementations should respect symbol boundaries: never split a
    function across chunks. The graph already knows where the
    boundaries are."""

    def chunk(self, file: FileRef, source: bytes) -> Sequence[Chunk]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Produce vector embeddings for chunks/queries.

    Returning ``list[list[float]]`` rather than a numpy array keeps the
    Protocol implementation-free."""

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
    ) -> list[tuple[str, float]]:  # (chunk_id, score)
        ...

    def delete(self, ids: Sequence[str]) -> None: ...


@runtime_checkable
class Reranker(Protocol):
    """Cross-encoder rerank step (Stage 4 of the pipeline)."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[tuple[str, str]],  # (chunk_id, text)
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
