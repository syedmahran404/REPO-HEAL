"""Embedding providers (ADR-0005).

Two implementations:

* :class:`HashEmbeddingProvider` — deterministic, dependency-free,
  poor-quality but adequate for development and testing. Default.

* :class:`SentenceTransformersEmbeddingProvider` — wraps
  ``sentence-transformers``. Needs the ``[ml]`` extra installed.
  Good quality. Production choice.

Both implement :class:`~repoheal.retrieval.protocols.EmbeddingProvider`.

The Protocol seam means agents and the retrieval service are ignorant
of which is in use. Configuration via ``REPOHEAL_EMBEDDING_PROVIDER``.
"""

from __future__ import annotations

import hashlib
import math
import warnings
from collections.abc import Iterable
from typing import Any

from ..logging import get_logger
from .tokenize import tokenize_code

_log = get_logger(__name__)


# =============================================================================
# Hash-based deterministic embeddings
# =============================================================================


class HashEmbeddingProvider:
    """Deterministic feature-hashing embedder.

    For every code-like token in the text we hash to ``(bucket, sign)``
    and accumulate into a fixed-length vector, then L2-normalise. This
    is a classic "feature hashing" / "hashing trick" embedder and has
    surprisingly decent recall when paired with BM25 in the hybrid
    pipeline. Quality is poor for paraphrase queries; we use it as the
    *runtime substitute for development*, not as a retrieval substitute.

    Reproducible across processes and platforms because we use BLAKE2
    explicitly (no hash randomization).
    """

    def __init__(self, dimension: int = 128) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_chunks(self, texts: Iterable[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    # ------------------------------------------------------------------

    def _embed(self, text: str) -> list[float]:
        v = [0.0] * self._dimension
        for tok in tokenize_code(text):
            digest = hashlib.blake2s(tok.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimension
            sign = 1.0 if (digest[4] & 1) else -1.0
            v[bucket] += sign

        # L2 normalize so cosine similarity reduces to dot product.
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0.0:
            return v
        return [x / norm for x in v]


# =============================================================================
# sentence-transformers backed embeddings (optional)
# =============================================================================


class SentenceTransformersEmbeddingProvider:
    """Production embedder backed by ``sentence-transformers``.

    Defaults to ``BAAI/bge-small-en-v1.5`` for cost; pass another name
    via the ``model_name`` arg. The model is loaded lazily on first
    embedding call so importing this module doesn't pull a 400 MiB
    download into a unit-test process.
    """

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model_name = model_name
        self._model: Any | None = None
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        self._ensure_loaded()
        assert self._dimension is not None
        return self._dimension

    def embed_chunks(self, texts: Iterable[str]) -> list[list[float]]:
        self._ensure_loaded()
        return [list(map(float, vec)) for vec in self._model.encode(list(texts), normalize_embeddings=True)]  # type: ignore[union-attr]

    def embed_query(self, text: str) -> list[float]:
        self._ensure_loaded()
        return list(map(float, self._model.encode([text], normalize_embeddings=True)[0]))  # type: ignore[union-attr]

    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Install with `pip install repoheal[ml]` or pick the "
                "HashEmbeddingProvider for development."
            ) from exc

        with warnings.catch_warnings():
            # The library spams a few benign deprecation warnings on import.
            warnings.simplefilter("ignore", DeprecationWarning)
            self._model = SentenceTransformer(self._model_name)
        self._dimension = int(self._model.get_sentence_embedding_dimension())  # type: ignore[union-attr]
        _log.info(
            "embeddings.loaded",
            provider="sentence-transformers",
            model=self._model_name,
            dimension=self._dimension,
        )


def default_embedding_provider() -> HashEmbeddingProvider:
    """The default for tests and dev: deterministic, fast, no deps."""
    return HashEmbeddingProvider()


__all__ = [
    "HashEmbeddingProvider",
    "SentenceTransformersEmbeddingProvider",
    "default_embedding_provider",
]
