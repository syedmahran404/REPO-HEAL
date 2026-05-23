"""Hybrid retrieval orchestrator.

Wires together chunking, BM25, vector search, graph expansion, RRF
fusion, token-budget packing, and caching.

Lifecycle:

1. :meth:`index` builds the corpus once: chunks every parsed file,
   tokenises chunks, builds a BM25 index, embeds chunks, populates a
   vector store, builds a chunk↔graph map.
2. :meth:`search` runs the three-stage pipeline against the indexed
   corpus, fuses with RRF, packs into the token budget, returns chunks.

Everything is in-memory. Persistence is the next milestone — same
Protocol, swap the backends.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import Chunk, ParsedFile, Repository
from ..core.protocols import GraphBackend
from ..logging import get_logger
from ..obs.tracing import traced
from .bm25 import BM25Index
from .cache import LRUCache, make_cache_key
from .chunking import SymbolAwareChunker
from .embeddings import HashEmbeddingProvider
from .fusion import rrf_fuse
from .graph_expansion import ChunkGraphIndex, GraphExpansion
from .packing import TokenBudgetPacker
from .protocols import EmbeddingProvider, VectorStore
from .tokenize import tokenize_code, tokenize_query
from .vector_store import InMemoryVectorStore

_log = get_logger(__name__)


@dataclass
class RetrievalTimings:
    """Per-stage timing for one retrieval call. Always emitted to logs."""

    chunk_lookup_ms: float = 0.0
    bm25_ms: float = 0.0
    vector_ms: float = 0.0
    fusion_ms: float = 0.0
    expansion_ms: float = 0.0
    packing_ms: float = 0.0
    total_ms: float = 0.0


@dataclass
class RetrievalResult:
    """Full result of a search: chunks plus per-stage diagnostics."""

    chunks: list[Chunk]
    timings: RetrievalTimings = field(default_factory=RetrievalTimings)
    cache_hit: bool = False


class RetrievalService:
    """Top-level retrieval entry point.

    The service is per-repository. Caller builds one, calls
    :meth:`index` once, then :meth:`search` many times.
    """

    def __init__(
        self,
        *,
        embeddings: EmbeddingProvider | None = None,
        vector_store: VectorStore | None = None,
        chunker: SymbolAwareChunker | None = None,
        cache_size: int = 256,
    ) -> None:
        self._embeddings: EmbeddingProvider = embeddings or HashEmbeddingProvider()
        self._vector_store: VectorStore = vector_store or InMemoryVectorStore()
        self._chunker = chunker or SymbolAwareChunker()
        self._packer = TokenBudgetPacker()
        self._cache: LRUCache[str, RetrievalResult] = LRUCache(cache_size)

        # State populated by `index()`.
        self._repo_id: str | None = None
        self._chunks_by_id: dict[str, Chunk] = {}
        self._chunk_id_order: list[str] = []
        self._bm25: BM25Index | None = None
        self._chunk_graph_index: ChunkGraphIndex | None = None
        self._graph: GraphBackend | None = None

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index(
        self,
        repo: Repository,
        parsed_files: Iterable[ParsedFile],
        graph: GraphBackend | None = None,
    ) -> int:
        """Build the corpus from parsed files. Returns chunk count.

        Idempotent for the same inputs. Calling again with new inputs
        replaces the corpus."""
        self._repo_id = str(repo.id)
        self._cache.clear()

        chunks: list[Chunk] = []
        for parsed in parsed_files:
            try:
                source = (repo.root / parsed.file.path).read_bytes()
            except OSError as exc:
                _log.warning(
                    "retrieval.read_failed",
                    path=str(parsed.file.path),
                    error=str(exc),
                )
                continue
            chunks.extend(self._chunker.chunk_parsed(parsed, source))

        # Persist chunk lookup tables.
        self._chunks_by_id = {c.id: c for c in chunks}
        self._chunk_id_order = [c.id for c in chunks]

        # BM25.
        token_streams = [tokenize_code(c.text) for c in chunks]
        self._bm25 = BM25Index(token_streams)

        # Vector store.
        if chunks:
            vectors = self._embeddings.embed_chunks(c.text for c in chunks)
            self._vector_store.upsert([c.id for c in chunks], vectors)

        # Graph linkage.
        self._graph = graph
        if graph is not None:
            self._chunk_graph_index = ChunkGraphIndex.build(chunks, graph)

        _log.info(
            "retrieval.indexed",
            repo_id=self._repo_id,
            chunks=len(chunks),
            embedding_dim=self._embeddings.dimension,
        )
        return len(chunks)

    # ------------------------------------------------------------------
    # Searching
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        token_budget: int | None = None,
        graph_expand: bool = True,
    ) -> RetrievalResult:
        """Run the three-stage hybrid pipeline."""
        if self._bm25 is None or self._repo_id is None:
            raise RuntimeError("RetrievalService.index() must be called first")

        cache_key = make_cache_key(self._repo_id, query, top_k, token_budget)
        cached = self._cache.get(cache_key)
        if cached is not None:
            cached.cache_hit = True
            return cached

        with traced(
            "retrieval.search",
            top_k=top_k,
            token_budget=token_budget if token_budget is not None else -1,
        ):
            timings = RetrievalTimings()
            t0 = time.perf_counter()

            # Stage 1: BM25.
            t1 = time.perf_counter()
            with traced("retrieval.bm25"):
                query_tokens = tokenize_query(query)
                bm25_top = self._bm25.search(query_tokens, top_k=max(top_k * 4, 20))
                bm25_ranked_ids = [self._chunk_id_order[idx] for idx, _score in bm25_top]
            timings.bm25_ms = (time.perf_counter() - t1) * 1000.0

            # Stage 2: vectors.
            t2 = time.perf_counter()
            with traced("retrieval.vector"):
                query_vec = self._embeddings.embed_query(query)
                vector_top = self._vector_store.search(query_vec, top_k=max(top_k * 4, 20))
                vector_ranked_ids = [vid for vid, _score in vector_top]
            timings.vector_ms = (time.perf_counter() - t2) * 1000.0

            # Fusion.
            t3 = time.perf_counter()
            fused = rrf_fuse([bm25_ranked_ids, vector_ranked_ids])
            timings.fusion_ms = (time.perf_counter() - t3) * 1000.0

            fused_ids = [cid for cid, _ in fused[: max(top_k * 2, 10)]]

            # Stage 3: graph expansion.
            t4 = time.perf_counter()
            with traced("retrieval.graph_expand"):
                if graph_expand and self._graph is not None and self._chunk_graph_index is not None:
                    expander = GraphExpansion(self._graph, self._chunk_graph_index)
                    extra = expander.expand(fused_ids[: max(top_k, 5)], hops=1, max_added=top_k)
                    for cid in extra:
                        if cid not in fused_ids:
                            fused_ids.append(cid)
            timings.expansion_ms = (time.perf_counter() - t4) * 1000.0

            # Materialise chunks.
            ranked_chunks_with_scores: list[tuple[Chunk, float]] = []
            score_for: dict[str, float] = {cid: s for cid, s in fused}
            for cid in fused_ids:
                chunk = self._chunks_by_id.get(cid)
                if chunk is None:
                    continue
                ranked_chunks_with_scores.append((chunk, score_for.get(cid, 0.0)))

            # Trim to top_k before packing.
            ranked_chunks_with_scores = ranked_chunks_with_scores[:top_k]

            # Pack into token budget.
            t5 = time.perf_counter()
            with traced("retrieval.pack"):
                packed = self._packer.pack(ranked_chunks_with_scores, token_budget=token_budget)
            timings.packing_ms = (time.perf_counter() - t5) * 1000.0

            timings.total_ms = (time.perf_counter() - t0) * 1000.0

            result = RetrievalResult(chunks=packed, timings=timings, cache_hit=False)
            self._cache.put(cache_key, result)

            _log.info(
                "retrieval.search_done",
                repo_id=self._repo_id,
                query_len=len(query),
                chunks=len(packed),
                total_ms=round(timings.total_ms, 2),
            )
            return result

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def chunk_count(self) -> int:
        return len(self._chunks_by_id)

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        return self._chunks_by_id.get(chunk_id)


__all__ = ["RetrievalResult", "RetrievalService", "RetrievalTimings"]
