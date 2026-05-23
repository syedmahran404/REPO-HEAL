"""Retrieval subsystem — Phase 2.

The architecture is the three-stage hybrid pipeline from
:doc:`docs/decisions/ADR-0003-retrieval-architecture`:

  BM25 + dense + graph expansion → RRF fusion → token-budget pack.

Public API:

* :class:`RetrievalService` — top-level orchestrator. Construct one,
  ``index(repo, parsed_files, graph)`` once, then ``search(query)``.
* :class:`SymbolAwareChunker`, :class:`BM25Index`,
  :class:`InMemoryVectorStore`, :class:`HashEmbeddingProvider`,
  :class:`SentenceTransformersEmbeddingProvider` — concrete components,
  swappable behind the Protocols in :mod:`repoheal.retrieval.protocols`.
* :func:`tokenize_code`, :func:`tokenize_query` — identifier-aware
  tokenization used by every stage.

Embedding strategy and choice of provider documented in ADR-0005.
"""

from .bm25 import BM25Index
from .cache import LRUCache, make_cache_key
from .chunking import ChunkingConfig, SymbolAwareChunker
from .embeddings import (
    HashEmbeddingProvider,
    SentenceTransformersEmbeddingProvider,
    default_embedding_provider,
)
from .fusion import rrf_fuse
from .graph_expansion import ChunkGraphIndex, GraphExpansion
from .packing import TokenBudgetPacker
from .protocols import (
    Chunker,
    EmbeddingProvider,
    HybridRetriever,
    Reranker,
    VectorStore,
)
from .service import RetrievalResult, RetrievalService, RetrievalTimings
from .tokenize import (
    estimate_token_count,
    split_identifier,
    tokenize_code,
    tokenize_query,
)
from .vector_store import InMemoryVectorStore

__all__ = [
    "BM25Index",
    "ChunkGraphIndex",
    "Chunker",
    "ChunkingConfig",
    "EmbeddingProvider",
    "GraphExpansion",
    "HashEmbeddingProvider",
    "HybridRetriever",
    "InMemoryVectorStore",
    "LRUCache",
    "Reranker",
    "RetrievalResult",
    "RetrievalService",
    "RetrievalTimings",
    "SentenceTransformersEmbeddingProvider",
    "SymbolAwareChunker",
    "TokenBudgetPacker",
    "VectorStore",
    "default_embedding_provider",
    "estimate_token_count",
    "make_cache_key",
    "rrf_fuse",
    "split_identifier",
    "tokenize_code",
    "tokenize_query",
]
