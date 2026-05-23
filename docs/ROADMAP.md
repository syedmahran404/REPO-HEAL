# REPO-HEAL — Roadmap & Phase Status

> Single source of truth for what is real, what is interface-only, and what has not been started.
>
> Legend:
> - **IMPL** — implemented, tested, runnable today.
> - **IFACE** — `Protocol`/ABC defined; no concrete backend yet.
> - **PLAN** — neither implementation nor interface; deferred by design.

## Phase Matrix

| # | Phase | Status | Where in repo | Notes |
|---|-------|--------|---------------|-------|
| 1 | Core architecture | **IMPL** | `docs/ARCHITECTURE.md`, `src/repoheal/core/` | Domain models + Protocols, expanded in Phase 2 with `Chunk`, `RepairAttempt`, `RankingScore`, `StackFrame`. |
| 2 | Repository ingestion | **IMPL** | `src/repoheal/ingestion/` | Local + git clone, ecosystem detection (11 langs), gitignore-aware walking. |
| 3 | Parsing + code intelligence | **IMPL** (Python full), **IFACE** (10 others) | `src/repoheal/intelligence/` | Phase 2 added Python identifier resolution → real `CALLS`, `INHERITS`, `REFERENCES` edges in the graph. |
| 4 | Knowledge graph | **IMPL** | `src/repoheal/graph/` | NetworkX backend; cycle detection, impact analysis, **call graph (Phase 2)**, **inheritance graph (Phase 2)**. Neo4j backend remains the swap target. |
| 5 | Retrieval system | **IMPL** | `src/repoheal/retrieval/` | **Phase 2:** symbol-aware chunker, identifier-tokenizing BM25, in-memory vector store, hash-based & sentence-transformers embedding providers (the latter optional), reciprocal rank fusion, graph expansion, token-budget packing, LRU cache, telemetry. |
| 6 | Agent orchestration | **IMPL** (runtime + 1 real agent), **IFACE** (LLM-backed agents) | `src/repoheal/agents/` | **Phase 2:** real `Orchestrator`, `AgentRunner`, `MemoryBus`, `ExecutionDAG`, durable JSON state. `RootCauseAgent` is fully implemented and uses graph + retrieval — no LLM required. LLM-backed agents are next, gated on an `LLMClient` Protocol. |
| 7 | Bug detection | **IMPL** (8 rules) | `src/repoheal/detection/` | **Phase 2:** added `unused_imports`, `mutable_default_args`, `broad_except`, `dead_code`, `long_method`, `god_class`, `hardcoded_secret`. Every rule emits `confidence ∈ [0,1]` and explainable metadata. |
| 8 | Patch generation | **IMPL** (mechanics + ranking) | `src/repoheal/patching/` | **Phase 2:** added `PatchRanker` with composable scorers (validation, diff size, graph impact, style consistency). LLM-driven generation lives in agents. |
| 9 | Validation infrastructure | **IMPL** (4 validators) | `src/repoheal/validation/` | **Phase 2:** added `RuffLintValidator`, `MypyTypeValidator`, `PytestValidator` running inside the sandbox runner with proper output parsing and tool-missing fallback. |
| 10 | Autonomous execution loops | **IMPL** (framework), **PLAN** (LLM-backed fixers) | `src/repoheal/repair/` | **Phase 2:** `RepairLoop` with bounded retries, validation-gated commits, snapshot rollback. The fix-generator interface is real; the only fixer shipping is rule-based (no LLM dependency). |
| 11 | GitHub issue solving | **IMPL** (ingestion + correlation), **PLAN** (auto-fix) | `src/repoheal/issues/` | **Phase 2:** real `GitHubIssueSource` over httpx, stack-trace extraction, traceback → graph correlation, issue → candidate-files via retrieval. PR creation deferred. |
| 12 | Self-learning | **PLAN** | — | ADR-0005 will pick the strategy once we have a corpus of accepted/rejected patches to learn from. |
| 13 | Frontend platform | **PLAN** | — | Backend OpenAPI is the contract; frontend is a separate codebase. |
| 14 | Production hardening | **PARTIAL** | various | Phase 2 added OpenTelemetry tracer init, `traced` decorator, spans on hot paths. Docker sandbox / Postgres state / Redis queue still planned. |

## Cross-cutting status

| Concern | Status | Notes |
|---------|--------|-------|
| Structured logging | **IMPL** | structlog, JSON in prod, KV in dev. |
| OpenTelemetry | **IMPL** (in-process) | Tracer + `traced` context manager + spans on ingest, parse, graph build, retrieval, agent runs, validation. Exporters are configured via `REPOHEAL_OTLP_ENDPOINT`. |
| Metrics (Prometheus) | **PLAN** | Mount point reserved on FastAPI app. |
| CI | **IMPL** (skeleton) | Python 3.11/3.12 matrix; ruff + black + mypy + pytest. |
| Pre-commit | **IMPL** | ruff, black, mypy. |
| Type checking | **IMPL** | mypy strict on `src/repoheal/`. |
| Docs site | **PLAN** | mkdocs-material is the planned tool. |

## What you can actually do today (Phase 2 deliverable)

After `pip install -e ".[dev]"`:

```bash
# Phase 1 — still works
repoheal ingest --path ./tests/fixtures/tiny_repo
repoheal scan   --path ./tests/fixtures/tiny_repo
repoheal graph build  --path ./tests/fixtures/tiny_repo --out graph.json

# Phase 2 — new
repoheal search --path ./tests/fixtures/tiny_repo --query "circular import detection" --top-k 5
repoheal agent  run  --path ./tests/fixtures/tiny_repo --agent root_cause --finding-id <id>
repoheal repair --path ./tests/fixtures/tiny_repo --rule unused_imports
repoheal trace  --path ./tests/fixtures/tiny_repo --traceback ./error.log

# API surface (new endpoints in this phase)
#   POST /retrieval/search          { path, query, top_k, token_budget? }
#   POST /agents/run                { path, agent, inputs, budget? }
#   POST /patches/rank              { path, candidates: [...] }
#   POST /issues/correlate          { path, issue_body }
#   GET  /telemetry/spans/recent
```

## Order of next implementation work (Phase 3 priorities)

1. **LLM client Protocol + provider adapters** (Anthropic, OpenAI). Gates LLM-backed agents.
2. **Docker sandbox**, replacing the subprocess runner under the same Protocol.
3. **JS/TS symbol extractors** to expand language coverage.
4. **Persistent state**: Postgres for jobs + findings + agent runs.
5. **Distributed execution**: Redis queue + worker pool.
6. **Neo4j graph backend** when in-memory NetworkX hits the trip-wires.

Each is one focused PR.
