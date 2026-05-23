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
| 1 | Core architecture | **IMPL** | `docs/ARCHITECTURE.md`, `src/repoheal/core/` | Domain models + Protocols ship with this PR. |
| 2 | Repository ingestion | **IMPL** | `src/repoheal/ingestion/` | Local & git clone, ecosystem detection (11 langs), gitignore-aware walking. |
| 3 | Parsing + code intelligence | **IMPL** (Python), **IFACE** (10 others) | `src/repoheal/intelligence/` | Tree-sitter facade complete; Python symbol extractor complete; other languages registered with `NotImplementedError` and link to add. |
| 4 | Knowledge graph | **IMPL** | `src/repoheal/graph/` | NetworkX backend; impact analysis, cycle detection, shortest path. Neo4j backend is the next swap target. |
| 5 | Retrieval system | **IFACE** | `src/repoheal/retrieval/` | Protocols only. ADR-0003 sets the design. Implementation gated on choice of embedding provider. |
| 6 | Agent orchestration | **IFACE** | `src/repoheal/agents/` | `Agent`, `AgentResult`, `Plan`, `PlannerAgent` Protocols defined. State machine and LLM client are the next milestones. |
| 7 | Bug detection | **IMPL** (1 rule), **IFACE** (registry) | `src/repoheal/detection/` | `CircularImportRule` is real and end-to-end tested. Rule registry is real. 9 other rules listed in `detection/rules/__init__.py` as `# TODO` with a one-line spec each. |
| 8 | Patch generation | **IMPL** (mechanics), **PLAN** (LLM-driven generation) | `src/repoheal/patching/` | Diff generation and safe apply/rollback are real. The "what to change" decision lives in agents (Phase 6). |
| 9 | Validation infrastructure | **IMPL** (pipeline + 1 validator) | `src/repoheal/validation/` | Pipeline orchestrator with short-circuit + aggregation. Python syntax validator real. Lint/type/test/build/security validators are one wrapper class each. |
| 10 | Autonomous execution loops | **IFACE** | `src/repoheal/agents/`, `src/repoheal/jobs/` | Loop is described in `ARCHITECTURE.md §6.2`. Implementation depends on Phase 6. |
| 11 | GitHub issue solving | **IFACE** | `src/repoheal/issues/` | `IssueSource` Protocol + `Issue` model defined. GitHub adapter pending auth model decision. |
| 12 | Self-learning | **PLAN** | — | ADR-0005 (later) decides between fine-tuning, prompt-cache, and vector memory. |
| 13 | Frontend platform | **PLAN** | — | Backend OpenAPI is the contract; frontend can be built independently. |
| 14 | Production hardening | **PLAN** | — | Docker sandbox, rate limits, secret scanning, OpenTelemetry exporter, distributed tracing wiring. |

## Cross-cutting status

| Concern | Status | Notes |
|---------|--------|-------|
| Structured logging | **IMPL** | structlog, JSON in prod, KV in dev. |
| OpenTelemetry | **IFACE** | Tracer/meter providers are wired; no exporter configured. |
| Metrics (Prometheus) | **PLAN** | Mount point reserved on FastAPI app. |
| CI | **IMPL** (skeleton) | `.github/workflows/ci.yml` runs lint + tests on Python 3.11/3.12. |
| Pre-commit | **IMPL** | ruff, black, mypy. |
| Type checking | **IMPL** | mypy strict on `src/repoheal/`. |
| Docs site | **PLAN** | mkdocs-material is the planned tool. |

## What you can actually do today (Phase 1 deliverable)

After installing this package locally (`pip install -e ".[dev]"`):

```bash
# 1. Ingest a local repo and emit its ecosystem fingerprint
repoheal ingest --path ./tests/fixtures/tiny_repo

# 2. Build the knowledge graph and dump it as JSON
repoheal graph build --path ./tests/fixtures/tiny_repo --out graph.json

# 3. Run the circular-import detector
repoheal scan --path ./tests/fixtures/tiny_repo --rule circular_imports

# 4. Run the API
uvicorn repoheal.api.main:app --reload
# then: POST /repositories/ingest with {"path": "..."} → returns Repository + Ecosystem + graph stats
```

Anything beyond that list is, today, vapor. The roadmap above is the path from vapor to real.

## Order of next implementation work

1. **Add language extractors for JS/TS** (highest leverage; unlocks half the candidate target repos).
2. **Wire up `LintValidator(ruff)` and `UnitTestValidator(pytest)`** — both are 50-line validators behind the existing pipeline.
3. **Implement the Docker sandbox** before any LLM-generated code is executed against a third-party repo.
4. **Pick an embedding provider and implement `FAISSVectorStore`** — needed before the agent system can be honest.
5. **Design the durable-execution agent state machine** (ADR-0004) and implement the `RootCauseAgent` first because it has the cleanest input/output contract.

Each of these is one focused PR.
