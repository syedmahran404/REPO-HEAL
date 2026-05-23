"""Identifier-aware tokenization for code search.

The single most important step in making BM25 useful for code is to
*split identifiers* the way a developer reading the code would: pull
``parse_args`` apart into ``parse`` and ``args``, ``MyHTTPClient`` into
``my``, ``http``, ``client``, ``a.b.c`` into ``a``, ``b``, ``c``, and
keep the original alongside.

This module exposes :func:`tokenize_code` and :func:`tokenize_query`.
Both go through the same pipeline; we keep two functions for clarity
and so a future tweak (e.g. expanding query tokens with synonyms) can
diverge cleanly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

# Whole-identifier finder. We deliberately permit dots so a dotted path
# like ``pkg.mod.Class`` is captured as one identifier first, then split.
_IDENT_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z0-9_\.]*")

# camelCase splitter. The alternation handles three cases:
#   (1) Lower-then-upper:  parseArgs   -> parse, Args
#   (2) Acronym + word:    HTTPParser  -> HTTP, Parser
#   (3) Trailing acronym:  parseHTTP   -> parse, HTTP
_CAMEL_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+|[0-9]+"
)

# A tiny stop list. Code has very different stopword profiles than English
# (the BM25 IDF will down-weight ``self`` naturally), so we keep this small.
_STOP: Final[frozenset[str]] = frozenset(
    {"the", "a", "an", "is", "of", "to", "in", "on", "and", "or"}
)


def split_identifier(ident: str) -> list[str]:
    """Split ``foo_bar.BazQux`` into [``foo``, ``bar``, ``baz``, ``qux``],
    plus the dotted segments and the original — all lowercased."""
    parts: list[str] = []
    seen: set[str] = set()

    def add(t: str) -> None:
        t = t.lower()
        if not t or len(t) < 2 or t in seen:
            return
        seen.add(t)
        parts.append(t)

    add(ident)
    for dotted in ident.split("."):
        add(dotted)
        for snake in dotted.split("_"):
            add(snake)
            for camel in _CAMEL_RE.findall(snake):
                add(camel)
    return parts


def tokenize_code(text: str) -> list[str]:
    """Tokenize a chunk of source for BM25 indexing.

    Order does not matter (BM25 is a bag-of-words model); we keep
    duplicates so term frequency is preserved.
    """
    out: list[str] = []
    for match in _IDENT_RE.finditer(text):
        ident = match.group(0)
        for tok in split_identifier(ident):
            if tok in _STOP:
                continue
            out.append(tok)
    return out


def tokenize_query(text: str) -> list[str]:
    """Tokenize a user query for BM25 lookup. Same pipeline as the corpus
    tokenizer to guarantee term-space alignment."""
    return tokenize_code(text)


def estimate_token_count(text: str) -> int:
    """Cheap heuristic for LLM-style token counts: ~4 chars per token.

    Used by the token-budget packer. We never feed this to a real model;
    it's only used to decide what to include in retrieval results."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def to_unique_tokens(tokens: Iterable[str]) -> list[str]:
    """Order-preserving dedup. Useful for queries where TF doesn't matter."""
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


__all__ = [
    "estimate_token_count",
    "split_identifier",
    "to_unique_tokens",
    "tokenize_code",
    "tokenize_query",
]
