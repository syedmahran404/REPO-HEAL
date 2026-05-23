# ADR-0005 — Two-tier embedding strategy with deterministic fallback

- **Status:** Accepted
- **Date:** 2026-05-23 (Phase 2)
- **Decision drivers:** test determinism, install footprint, ability to ship a working retrieval system without 2 GiB of model weights.

## Context

Hybrid retrieval (ADR-0003) needs an `EmbeddingProvider`. The production choice is a code-aware sentence-transformer model (~400 MiB on disk). The development and test choice cannot require that — we want `pytest` to run in <60 seconds on a fresh checkout, and we don't want every contributor to download model weights to verify a one-line change.

## Decision

Ship **two** concrete `EmbeddingProvider`s behind the Protocol:

1. **`HashEmbeddingProvider`** (default in `dev`/`test`). Deterministic, dependency-free, no model weights. It hashes identifier-tokens to a fixed-dimension float vector with sign and bucket determined by `xxhash`-style mixing. Quality is poor for paraphrase queries but adequate for identifier-heavy code search; combined with BM25 in the hybrid pipeline, retrieval quality is usable for development.

2. **`SentenceTransformersEmbeddingProvider`** (production). Wraps `sentence-transformers` with a configurable model name (default: `BAAI/bge-small-en-v1.5` for cost, swap to `bge-large-en-v1.5` for quality). Installed via the `[ml]` extra so users who don't need it never download it.

Tests run against the hash provider. Production installs the `[ml]` extra and switches via configuration (`REPOHEAL_EMBEDDING_PROVIDER=sentence_transformers`). The Protocol seam means agents are ignorant of which is in use.

### Why hash, not "no embeddings at all"

We wanted Phase 2's retrieval system to be testable end-to-end without optional deps. A no-op embedding provider would force every test to either skip the vector lane or `importorskip` sentence-transformers. The hash provider lets us exercise the full pipeline (chunking → embedding → storage → similarity → fusion) with deterministic, fast, license-free code.

The hash provider is **not a retrieval substitute**. It is a *runtime substitute for development*. The README and CLI emit a warning when it is in use.

## Consequences

**Positive:**
- Tests run fast and deterministic.
- Default installation is small.
- Production install is opt-in.
- CI does not need to download model weights.

**Negative:**
- Two provider implementations to maintain. The hash one is ~80 LoC and changes rarely; the sentence-transformers wrapper is ~50 LoC. Manageable.
- Risk of devs developing against the wrong provider and being surprised in production. Mitigation: the warning, plus integration tests gated behind `RUN_LLM_TESTS=1` that exercise the real provider.

## Out of scope

- Cloud-hosted embedding APIs (OpenAI, Voyage, Cohere). Adding one is a single new provider class behind the Protocol; we will when a customer explicitly asks.
- Multi-modal embeddings.
- Fine-tuning. Deferred to Phase 12.
