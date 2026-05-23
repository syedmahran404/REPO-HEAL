# ADR-0003 — Three-stage hybrid retrieval (BM25 + vector + graph) with RRF fusion

- **Status:** Accepted (interface-defined; implementation pending)
- **Date:** 2026-05-23
- **Decision drivers:** code-search accuracy, no tuning hyperparameters, robustness across query styles.

## Context

Code retrieval has properties that punish naive approaches:

- **Identifier sensitivity.** A query mentioning `parse_args` should match a function literally named `parse_args`. Pure semantic search often misses this in favor of "similar concept" matches.
- **Paraphrase tolerance.** A query like "function that handles flag parsing" should also match `parse_args`. Pure lexical search misses this.
- **Architectural relevance.** Top-k by similarity is not enough. If the user asked about `parse_args` and the bug is in its caller, we need the caller in the context window.

## Decision

Three-stage retrieval pipeline:

```
query
  │
  ├─▶ Stage 1: BM25 over identifier-aware tokenization     ──┐
  │      (splits camelCase, snake_case, dotted paths)        │
  │                                                          │
  ├─▶ Stage 2: Dense vector retrieval                       ──┤── RRF fusion ──▶ rerank ──▶ pack
  │      (chunk-level embeddings, cosine)                    │   (reciprocal     (cross-      (token
  │                                                          │    rank fusion)    encoder)     budget)
  └─▶ Stage 3: Graph expansion of stage-1+2 candidates ────┘
         (add 1-hop CALLERS and CALLEES for any function
          in the merged top-k)
```

### Why RRF (Reciprocal Rank Fusion)

RRF combines ranked lists without weight tuning: `score(d) = Σ 1/(k + rank_i(d))`. It is provably robust when the input rankers are reasonably good and at least somewhat independent — exactly our situation with lexical vs. semantic. The alternative (weighted sum of normalized scores) needs constant tuning and silently drifts when the embedding model changes.

### Why a separate graph-expansion stage

The graph encodes causal relationships ("X calls Y", "Y imports Z"). When debugging, the *cause* is rarely the symptom site; it is one or two hops upstream. Expanding stage-1+2 results along graph edges before reranking puts the actual root-cause locations into the candidate set. No similarity-based retriever does this.

### Why a cross-encoder rerank step

Bi-encoders (used in stage 2) are fast but imprecise. Reranking the top-100 with a cross-encoder takes ~50ms and sharply improves top-5 quality. We make this optional via configuration because it adds a model dependency.

## Consequences

**Positive:**
- Robust across query styles (identifier, NL, mixed).
- Architecturally aware — the prompt's "dependency-aware retrieval" requirement is met by stage 3, not by hand-waving.
- Each stage is independently testable and replaceable.

**Negative:**
- Three indexes to maintain (BM25 inverted index, vector store, graph). Incremental update logic must keep all three coherent. Mitigation: a single `IndexUpdater` that fans out to all three.
- Higher latency than vector-only. We measure and tune; if we ever need <50ms p95 retrieval, drop the rerank.

## Provider choices (deferred)

- **Vector store:** FAISS for in-process dev, Qdrant for production. `pgvector` if we want one less moving part and have already committed to Postgres.
- **Embeddings:** undecided. We will pick one of the open code-aware embedding models (e.g., a code-tuned BGE variant) so we are not vendor-locked. Wrapped behind `EmbeddingProvider` Protocol.
- **Reranker:** small cross-encoder, also wrapped behind a Protocol.

## Status

Protocols exist. Implementations do not. This ADR exists so that when we implement, the design is not re-litigated mid-PR.
