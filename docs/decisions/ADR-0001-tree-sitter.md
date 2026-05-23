# ADR-0001 — Use tree-sitter as the universal parsing layer

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decision drivers:** multi-language support breadth, parsing uniformity, incremental re-parse, mature ecosystem.

## Context

REPO-HEAL must parse 11+ languages (Python, JS, TS, Java, Go, Rust, C, C++, C#, PHP, Kotlin, Swift, Ruby) and turn source into structured symbols, imports, calls. Three options:

1. **Per-language native parsers** — Python `ast`, Babel for JS, javalang for Java, `go/parser` via subprocess, `syn` via Rust subprocess, etc. Highest fidelity per language; highest integration cost; non-uniform output models we'd have to normalize.
2. **Language Server Protocol (LSP) per language** — accurate semantic info; requires running a language server per language per analysis (heavyweight); cold-start latency is brutal at ingestion scale.
3. **Tree-sitter** — single C library, Python bindings, ~150+ grammars maintained, incremental parsing, query language, byte-offset accurate.

## Decision

**Adopt tree-sitter as the primary parsing layer.** Allow per-language native parsers as supplementary "deep" extractors when tree-sitter cannot answer a question (e.g., type inference). Native parsers are plugged in behind the same `LanguageDefinition` and called only when needed.

## Consequences

**Positive:**
- One API across all supported languages.
- Tree-sitter queries (`.scm` files) are declarative and reviewable; symbol extraction logic is data, not code.
- Incremental parsing is free, which becomes essential when we move to "re-parse only changed files since last commit" in Phase 4 of incremental indexing.
- Active community, well-maintained Rust/Python/JS bindings.

**Negative:**
- Tree-sitter doesn't do semantic analysis (no type resolution, no name binding). For accurate cross-file `CALLS` edges in dynamically-typed languages we will need a second pass — for Python this means wrapping `jedi` or `pyright`'s indexer.
- Some grammars are less mature than others; quality varies.
- Native bindings mean a binary dependency. We pin via `tree-sitter-languages` which ships precompiled wheels for the major platforms.

## Alternatives rejected

- **Pure LSP-based approach** — too slow for batch ingestion; useful as a future supplementary signal.
- **Roll our own ANTLR-based parsers** — re-inventing tree-sitter, which has had 10 years of grammar contributions we'd lose.

## Notes

The `LanguageDefinition` Protocol is designed so a future "use the real type-aware parser for Python" change is a single new class, not a rewrite of the intelligence layer.
