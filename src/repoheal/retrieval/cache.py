"""LRU cache for retrieval results.

Bounded by entry count, not by memory. Adequate for development; a
production deployment would back this with Redis (single-line swap
behind a Protocol when we add it).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class LRUCache(Generic[K, V]):
    """Tiny LRU. Move-on-access semantics; thread-safe enough for the
    GIL-bound paths we use it on (single-process, single-thread inside
    one request)."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._store: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K) -> V | None:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def put(self, key: K, value: V) -> None:
        if key in self._store:
            self._store.move_to_end(key)
            self._store[key] = value
            return
        self._store[key] = value
        if len(self._store) > self._capacity:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: object) -> bool:
        return key in self._store


def make_cache_key(repo_id: str, query: str, top_k: int, token_budget: int | None) -> str:
    """Stable cache key for a retrieval request."""
    budget_str = str(token_budget) if token_budget is not None else "none"
    return f"{repo_id}::{top_k}::{budget_str}::{query}"


__all__ = ["LRUCache", "make_cache_key"]
