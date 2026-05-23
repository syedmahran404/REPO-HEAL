"""Unit tests for individual retrieval components.

These don't depend on tree-sitter so they run anywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repoheal.core.models import Chunk, FileRef, Language
from repoheal.retrieval import (
    BM25Index,
    HashEmbeddingProvider,
    InMemoryVectorStore,
    LRUCache,
    SymbolAwareChunker,
    TokenBudgetPacker,
    rrf_fuse,
    split_identifier,
    tokenize_code,
    tokenize_query,
)
from repoheal.retrieval.cache import make_cache_key


# --- tokenize -------------------------------------------------------------


def test_split_identifier_camel_and_snake() -> None:
    out = split_identifier("MyHTTPClient")
    assert "myhttpclient" in out
    assert "http" in out
    assert "client" in out
    out = split_identifier("parse_args")
    assert "parse" in out
    assert "args" in out
    assert "parse_args" in out


def test_split_identifier_dotted() -> None:
    out = split_identifier("pkg.mod.ClassName")
    assert "pkg" in out
    assert "mod" in out
    assert "classname" in out
    assert "class" in out
    assert "name" in out


def test_tokenize_code_handles_punctuation() -> None:
    tokens = tokenize_code("def parse_args(self) -> None:")
    assert "parse" in tokens
    assert "args" in tokens
    assert "parse_args" in tokens
    # 'self' keyword is short but >= 2 chars; we keep it (BM25 will down-weight it).
    assert "self" in tokens


def test_tokenize_query_aligned_with_tokenize_code() -> None:
    # The pipelines should be byte-identical so BM25 IDF is meaningful.
    q = "parse arguments"
    assert tokenize_query(q) == tokenize_code(q)


# --- BM25 -----------------------------------------------------------------


def test_bm25_finds_exact_identifier_match() -> None:
    docs = [
        tokenize_code("def parse_args(self): return None"),
        tokenize_code("def serve_request(req): return req"),
        tokenize_code("class TokenIndex: pass"),
    ]
    bm = BM25Index(docs)
    hits = bm.search(tokenize_query("parse_args"), top_k=3)
    assert hits[0][0] == 0  # the parse_args doc wins


def test_bm25_returns_empty_for_unknown_term() -> None:
    bm = BM25Index([tokenize_code("hello world")])
    assert bm.search(tokenize_query("xyz"), top_k=5) == []


def test_bm25_idf_smooth() -> None:
    # All docs contain 'foo' → its IDF is small (positive but small).
    bm = BM25Index([tokenize_code("foo bar")] * 3)
    idf = bm.idf("foo")
    assert idf >= 0.0


# --- embeddings -----------------------------------------------------------


def test_hash_embedding_is_deterministic() -> None:
    p = HashEmbeddingProvider(dimension=32)
    a = p.embed_query("def parse_args(): pass")
    b = p.embed_query("def parse_args(): pass")
    assert a == b
    assert len(a) == 32


def test_hash_embedding_normalized() -> None:
    p = HashEmbeddingProvider(dimension=64)
    v = p.embed_query("class Foo: ...")
    # L2 norm should be ~1 for any non-empty input.
    norm_squared = sum(x * x for x in v)
    assert 0.99 < norm_squared < 1.01


def test_hash_embedding_chunks_batch() -> None:
    p = HashEmbeddingProvider(dimension=16)
    vecs = p.embed_chunks(["a b c", "d e f"])
    assert len(vecs) == 2
    assert all(len(v) == 16 for v in vecs)


# --- vector store --------------------------------------------------------


def test_vector_store_upsert_and_search() -> None:
    p = HashEmbeddingProvider(dimension=32)
    store = InMemoryVectorStore()

    docs = {
        "a": "def parse_args(): pass",
        "b": "def serve(): pass",
        "c": "class TokenIndex: pass",
    }
    ids = list(docs)
    vecs = p.embed_chunks(docs.values())
    store.upsert(ids, vecs)

    results = store.search(p.embed_query("parse_args"), top_k=3)
    assert results[0][0] == "a"  # exact-match identifier dominates


def test_vector_store_delete() -> None:
    p = HashEmbeddingProvider(dimension=8)
    store = InMemoryVectorStore()
    store.upsert(["a", "b"], p.embed_chunks(["x", "y"]))
    assert len(store) == 2
    store.delete(["a"])
    assert len(store) == 1


def test_vector_store_search_empty() -> None:
    store = InMemoryVectorStore()
    assert store.search([0.0] * 8, top_k=5) == []


def test_vector_store_upsert_length_mismatch_raises() -> None:
    store = InMemoryVectorStore()
    with pytest.raises(ValueError):
        store.upsert(["a"], [[0.0], [1.0]])


# --- fusion --------------------------------------------------------------


def test_rrf_basic() -> None:
    bm = ["a", "b", "c"]
    vec = ["b", "a", "d"]
    fused = rrf_fuse([bm, vec])
    # Both rank 'a' high; 'b' second; 'c' and 'd' lower.
    fused_ids = [d for d, _ in fused]
    assert fused_ids[0] in {"a", "b"}
    assert "c" in fused_ids
    assert "d" in fused_ids


def test_rrf_handles_empty_inputs() -> None:
    assert rrf_fuse([]) == []
    assert rrf_fuse([[]]) == []


# --- packer --------------------------------------------------------------


def _chunk(cid: str, path: str, text: str) -> Chunk:
    return Chunk(
        id=cid,
        file_path=Path(path),
        text=text,
        start_line=0,
        end_line=text.count("\n"),
        language=Language.PYTHON,
    )


def test_packer_respects_token_budget() -> None:
    pk = TokenBudgetPacker(max_chunks_per_file=10)
    chunks = [
        (_chunk("a", "f1.py", "x" * 400), 0.9),  # ~100 tokens
        (_chunk("b", "f2.py", "y" * 400), 0.8),
    ]
    out = pk.pack(chunks, token_budget=100)
    # Only one fits.
    assert len(out) == 1
    assert out[0].id == "a"


def test_packer_per_file_cap() -> None:
    pk = TokenBudgetPacker(max_chunks_per_file=2)
    chunks = [(_chunk(f"x{i}", "same.py", "abcd" * 5), 1.0) for i in range(5)]
    out = pk.pack(chunks, token_budget=10_000)
    assert len(out) == 2  # cap


def test_packer_includes_oversized_first_chunk_when_no_budget() -> None:
    pk = TokenBudgetPacker()
    chunks = [(_chunk("a", "f.py", "huge" * 1_000), 1.0)]
    out = pk.pack(chunks, token_budget=None)
    assert len(out) == 1


# --- cache ---------------------------------------------------------------


def test_lru_evicts_oldest() -> None:
    c: LRUCache[str, int] = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)  # evicts "a"
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("c") == 3


def test_lru_get_promotes() -> None:
    c: LRUCache[str, int] = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")  # promote a
    c.put("c", 3)  # evicts b, not a
    assert c.get("b") is None
    assert c.get("a") == 1


def test_make_cache_key_includes_parameters() -> None:
    k1 = make_cache_key("repo1", "q", 5, None)
    k2 = make_cache_key("repo1", "q", 5, 100)
    k3 = make_cache_key("repo2", "q", 5, None)
    assert k1 != k2 != k3


# --- chunking ------------------------------------------------------------


def test_chunker_whole_file_fallback() -> None:
    fr = FileRef(path=Path("a.txt"), language=Language.UNKNOWN, size_bytes=10, is_binary=False)
    out = list(SymbolAwareChunker().chunk(fr, b"hello\nworld\n"))
    assert len(out) == 1
    assert out[0].text == "hello\nworld\n"
    assert out[0].id == "a.txt::__whole__"
