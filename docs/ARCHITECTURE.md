# REPO-HEAL — System Architecture

> Autonomous Repository Analysis & Self-Healing AI Engine
>
> **Status:** Phase 1 architecture finalized. Vertical slice implemented across phases 1–3, 6, 7, 9, 10, 11. Phases 5, 8, 12, 13, 14 are interface-defined only. See [ROADMAP.md](./ROADMAP.md) for the per-phase status matrix.

---

## 1. Executive Summary

REPO-HEAL is a multi-agent system that ingests a repository, builds a deep semantic understanding of it, identifies defects, generates safe fixes, validates them in a sandbox, and opens a pull request — all autonomously.

It is structured as **eleven independent subsystems** connected through:

- A typed **domain model** (`repoheal.core.models`)
- A **Protocol-based dependency-injection layer** (`repoheal.core.protocols`)
- An **event-driven orchestration plane** (planned for Phase 6; interface defined now)

The hard constraint that drove every architectural decision: **the system must remain useful even when individual subsystems are stubbed out.** A repository with no embeddings backend should still be ingestable, parseable, graph-able, and lintable. Every subsystem is therefore swappable at the seam, never at the call site.

---

## 2. Design Principles

| # | Principle | Concrete Consequence |
|---|-----------|----------------------|
| 1 | **Protocol-first, implementation-second** | Every subsystem boundary is a `typing.Protocol`. Tests inject fakes; production injects real backends. |
| 2 | **Side-effect quarantine** | All I/O (git, filesystem, LLM, DB, network) lives behind an interface in a `*/backends/` or `*/stores/` module. Domain logic is pure. |
| 3 | **Fail closed on patch generation** | A patch is *never* accepted unless every validator in its lane passes. Default verdict on validator error is `REJECT`, not `PASS`. |
| 4 | **Architectural awareness over token greed** | Retrieval ranks by graph proximity + semantic similarity, not just cosine distance. Tokens are a budget, not a default. |
| 5 | **Pure functions in the hot path** | Symbol extraction, graph construction, and impact analysis are pure (input → output, no globals). This makes them trivially parallelizable and testable. |
| 6 | **Evolvability beats optimality** | NetworkX over Neo4j *for now* — wrong choice for 10M-node graphs, right choice for shipping a tested seam in week one. The `GraphBackend` Protocol makes the swap a one-day job. |
| 7 | **No fake implementations** | If a subsystem is not built, it raises `NotImplementedError` with the issue tracker link. There are no `pass` bodies pretending to work. |

---

## 3. Subsystem Map

```
                             ┌──────────────────────────┐
                             │        Frontend          │  (Phase 13 — PLANNED)
                             │  React + TS + Tailwind   │
                             └────────────┬─────────────┘
                                          │ HTTP/SSE
                             ┌────────────▼─────────────┐
                             │      FastAPI Gateway     │  (IMPLEMENTED, minimal)
                             │   /repos /graph /agents  │
                             └────────────┬─────────────┘
                                          │
        ┌─────────────────────────────────┼─────────────────────────────────┐
        │                                 │                                 │
┌───────▼────────┐   ┌─────────────┐ ┌────▼────────┐   ┌────────────┐ ┌────▼─────────┐
│   Ingestion    │──▶│ Code Intel  │─▶│  Knowledge  │──▶│ Detection  │─▶│   Patching   │
│  (clone/walk/  │   │ (tree-sitter│ │   Graph     │   │   Rules    │  │ (diff/apply) │
│   detect)      │   │  + symbols) │ │ (NetworkX)  │   │ (circular  │  │              │
│  IMPLEMENTED   │   │ IMPLEMENTED │ │ IMPLEMENTED │   │  imports…) │  │ IMPLEMENTED  │
└────────────────┘   └─────────────┘ └─────────────┘   │ 1-of-N done│  └──────┬───────┘
                                                       └────────────┘         │
                              ┌─────────────────────────────────────────┐     │
                              │              Retrieval                   │     │
                              │  hybrid BM25+vector+graph (PROTOCOLS)   │     │
                              └─────────────────────────────────────────┘     │
                                          ▲                                    ▼
                                          │                            ┌────────────────┐
                              ┌───────────┴──────────┐                 │   Validation   │
                              │       Agents          │                 │  syntax / lint │
                              │ planner/repair/etc.   │                 │  /tests/build  │
                              │ (PROTOCOLS)           │                 │  IMPLEMENTED 1 │
                              └──────────────────────┘                 └───────┬────────┘
                                                                                │
                                                                       ┌────────▼────────┐
                                                                       │     Sandbox     │
                                                                       │ subprocess (✓)  │
                                                                       │ docker (PLAN)   │
                                                                       └─────────────────┘

   Cross-cutting:
   - Observability   (structlog ✓, OpenTelemetry hooks defined)
   - Persistence     (Postgres + object store, PROTOCOLS only)
   - Memory/Learning (PLANNED — Phase 12)
   - Issue Solver    (PLANNED — Phase 11, interface defined)
```

---

## 4. Subsystems in Detail

### 4.1 Repository Ingestion (`repoheal.ingestion`)

**Responsibility:** Get a repository onto local disk in a known state, identify what it is, and emit a `Repository` value object containing every file the rest of the system needs to know about.

**Components:**
- `cloner.GitCloner` — wraps `git`. Supports shallow clone, branch checkout, local-path "ingestion" (no clone). Backed by `subprocess` with strict timeouts. Returns a `RepositorySnapshot`.
- `detector.EcosystemDetector` — pure function `detect(root: Path) -> Ecosystem`. Examines manifest files (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `composer.json`, `Gemfile`, `*.csproj`, `Package.swift`, `CMakeLists.txt`) and emits a structured `Ecosystem(languages, frameworks, build_systems, package_managers, test_frameworks)` record.
- `walker.RepositoryWalker` — gitignore-aware filesystem walker (uses `pathspec`). Streams `FileRef` objects. Handles symlinks, binary detection (null-byte scan), size limits.
- `service.IngestionService` — orchestrates the three above, returns a fully-populated `Repository`.

**Why a service class instead of a function:** the ingestion process spans I/O boundaries that we will eventually want to instrument (telemetry), retry (transient git failures), and parallelize (multi-repo monorepos). A service object is the unit of observability and configuration.

### 4.2 Code Intelligence (`repoheal.intelligence`)

**Responsibility:** Turn source bytes into structured symbols, imports, calls, and relationships, language by language.

**Components:**
- `parser.TreeSitterParser` — single tree-sitter facade. Takes `(file_ref, source_bytes, language)` → `ParsedFile(tree, root_node)`. Caches grammars per-language. Thread-safe (tree-sitter parsers are not, so we use a `threading.local`).
- `languages.LanguageRegistry` — registry mapping file extensions and shebangs → `LanguageDefinition(name, tree_sitter_grammar, query_paths)`. 11 languages registered: Python, JS, TS, Java, Go, Rust, C, C++, C#, PHP, Kotlin, Swift, Ruby.
- `symbols.SymbolExtractor` — runs tree-sitter queries against a parsed file, emits `Symbol` records (functions, classes, methods, variables, imports). One concrete extractor per language; Python is fully implemented; others raise `NotImplementedError` with a clear path to add.
- `imports.ImportResolver` — resolves `import x.y` → file path, given a project's manifest layout. Critical for cross-file edges in the knowledge graph.

**Why tree-sitter over LSP/native parsers:** uniform API across 40+ languages, incremental parsing for free, no language server setup required. ADR-0001 records this decision in detail.

### 4.3 Knowledge Graph (`repoheal.graph`)

**Responsibility:** A queryable, typed, bidirectional graph of every architectural relationship in the repo.

**Node types:** `File`, `Module`, `Class`, `Function`, `Method`, `Variable`, `Import`, `Service`, `Endpoint`.
**Edge types:** `CONTAINS`, `IMPORTS`, `CALLS`, `INHERITS`, `IMPLEMENTS`, `RAISES`, `REFERENCES`, `READS`, `WRITES`.

**Components:**
- `schema.NodeKind`, `EdgeKind`, `Node`, `Edge` — dataclass-based, hashable, serializable.
- `backend.GraphBackend` — `Protocol` defining `add_node`, `add_edge`, `neighbors`, `subgraph`, `shortest_path`, `cycles`, `serialize`, `load`.
- `networkx_backend.NetworkXGraphBackend` — first concrete implementation. In-process `networkx.MultiDiGraph`. Good for repos up to ~1M nodes; beyond that we move to the Neo4j backend (planned, ADR-0002).
- `builder.GraphBuilder` — consumes `Symbol` and `Import` streams, emits nodes and edges. Pure transformation; backend-agnostic.
- `queries.ImpactAnalysis` — implements **change impact prediction**: given a node, find its transitive consumers up to depth N. **Root-cause tracing**: given a failure point, walk `CALLS` and `IMPORTS` backward. **Cycle detection**: surfaces architectural cycles (used by the circular-import rule).

**Why this is a hard problem we are solving and not punting on:** every other subsystem reads from the graph. Without it, "fix this bug" becomes "edit this file with no awareness of what else uses it" — i.e., the failure mode the prompt explicitly forbids.

### 4.4 Retrieval (`repoheal.retrieval`) — INTERFACE-DEFINED

**Responsibility:** Given a natural-language or code query and a repository context, return the top-k most architecturally relevant chunks within a token budget.

**Design:** 3-stage pipeline (defined as Protocols, not implemented).

```
Stage 1: Lexical recall (BM25)         ──┐
Stage 2: Semantic recall (vector)        ├──▶ fusion (RRF) ──▶ rerank ──▶ pack
Stage 3: Graph expansion (k-hop)       ──┘
```

**Why hybrid:** vector-only retrieval misses exact identifier matches (the bane of code search); BM25-only misses paraphrases and semantic intent. Reciprocal rank fusion combines them robustly without tuning weights. Graph expansion ensures we never return a function without its critical neighbors.

**Stores:** `VectorStore` Protocol with planned backends for FAISS (in-process), Qdrant (production), pgvector (transactional). ADR-0003 covers the choice.

**Why we did not implement this in Phase 1:** real retrieval requires a corpus + an embedding provider + storage. None of those are *correct* without the graph, which we are shipping first. Building retrieval before the graph would lock in the wrong ranking signals.

### 4.5 Multi-Agent System (`repoheal.agents`) — INTERFACE-DEFINED

**Responsibility:** Coordinate specialized agents (Architect, Bug Detector, Root Cause, Patch Generator, Validator, etc.) to solve a high-level goal like "fix issue #123."

**Design choice:** orchestration via a state machine, not free-form agent chatter. ADR-0004 (forthcoming) will compare LangGraph vs. a custom asyncio state machine; current lean is custom, because the prompt explicitly demands durable execution and we want to control the persistence model.

**Defined now:**
- `Agent` Protocol with `name`, `tools`, `run(state) -> AgentResult`.
- `AgentRegistry` for lookup.
- `PlannerAgent` interface — given a goal and repo state, produce a DAG of agent invocations.

**Not built:** the actual agents and the LLM client integration. This is deliberate. Building agents before the graph and validators are real produces fake agents; we have the infrastructure to plug them in cleanly when we do.

### 4.6 Detection (`repoheal.detection`)

**Responsibility:** Run rule-based and AI-based defect detectors over the parsed code + graph and emit `Finding` records.

**Architecture:** rule registry. Each rule implements `Rule.scan(repo, graph) -> list[Finding]`. The runtime is the same whether the rule is heuristic, static-analysis-based, or LLM-backed.

**Implemented in Phase 1:**
- `python_rules.CircularImportRule` — uses graph cycle detection on `IMPORTS` edges. Real, tested, end-to-end. This is the *proof* that the graph + intelligence pipeline works.

**Defined but not implemented:** unused-imports, mutable-default-args, broad-except, async-deadlock, n+1-query, race-condition, dead-code, long-method, god-class, missing-test-coverage. Each is one rule class away.

### 4.7 Patching (`repoheal.patching`)

**Responsibility:** Take a `Fix` (target file + new content / structured edit) and produce a unified diff, apply it transactionally, or roll it back.

**Components:**
- `diff.UnifiedDiffGenerator` — `difflib.unified_diff` wrapper with proper line-ending and context handling.
- `applier.PatchApplier` — applies a patch to a working tree, with snapshot-based rollback on any error from the validation pipeline. Uses `git apply --check` then `git apply` if a git repo, falls back to manual application otherwise.

**Hard rule encoded in code:** `applier.apply()` always takes a `Validator` callable and reverts the working tree if validation returns anything other than `Verdict.PASS`. There is no "apply and hope" path.

### 4.8 Validation (`repoheal.validation`)

**Responsibility:** Decide whether a patch is safe to merge.

**Design:** validators are composable. A `ValidationPipeline` is an ordered list of `Validator` callables that each return a `ValidationResult`. The pipeline short-circuits on the first `FAIL` (cheap checks first), aggregates all `WARN`s, and returns a final `Verdict`.

**Implemented:**
- `SyntaxValidator` for Python (uses `ast.parse`). Real, tested.
- `ValidationPipeline` orchestrator with short-circuit + aggregation.

**Interface-defined:** `LintValidator`, `TypeCheckValidator`, `UnitTestValidator`, `BuildValidator`, `SecurityScanValidator`. Each will be implemented as a thin wrapper around the canonical tool for that ecosystem (`ruff`, `mypy`, `pytest`, etc.) running inside the sandbox.

### 4.9 Sandbox (`repoheal.sandbox`)

**Responsibility:** Run untrusted code (the repo under analysis, generated patches, test suites) without compromising the host.

**Implemented:** `SubprocessRunner` — runs commands with `cwd`, `timeout`, env scrubbing, output capture, and a kill-after-timeout policy. Suitable for trusted local dev; **not** suitable for arbitrary GitHub repos.

**Planned:** `DockerSandbox` — same `SandboxRunner` Protocol, backed by short-lived containers with no network, dropped capabilities, read-only root, tmpfs work dir, resource limits (CPU/mem/pids/file descriptors).

The Protocol design means swapping subprocess → Docker is one line of dependency injection.

### 4.10 Issue Solver (`repoheal.issues`) — INTERFACE-DEFINED

**Responsibility:** Read a GitHub/GitLab issue, correlate it with repo state, produce a fix, validate, push a PR.

**Defined:**
- `IssueSource` Protocol — `fetch(repo, number) -> Issue`.
- `Issue` model — title, body, labels, comments, linked stack traces.
- The end-to-end flow as a sequence diagram in §6.

**Not built:** GitHub API client (we need an auth model first), the LLM-driven correlation, the PR creation step.

### 4.11 Self-Improvement (`repoheal.learning`) — PLANNED

Out of scope for Phase 1. ADR-0005 (later) will cover the choice between fine-tuning, prompt-cache learning, and a retrieval-augmented memory.

### 4.12 Observability (`repoheal.obs`)

**Implemented:** structured logging with `structlog`, JSON output in production, key-value in dev.
**Hooks defined:** OpenTelemetry tracer/meter providers, Prometheus exporter mount point on the FastAPI app.

---

## 5. Folder Structure

```
REPO-HEAL/
├── docs/
│   ├── ARCHITECTURE.md                ← this file
│   ├── ROADMAP.md
│   └── decisions/
│       ├── ADR-0001-tree-sitter.md
│       ├── ADR-0002-graph-backend.md
│       └── ADR-0003-retrieval-architecture.md
├── src/
│   └── repoheal/
│       ├── __init__.py
│       ├── config.py                  ← settings (pydantic-settings)
│       ├── logging.py                 ← structlog setup
│       ├── exceptions.py              ← exception hierarchy
│       ├── core/
│       │   ├── models.py              ← Pydantic domain models
│       │   └── protocols.py           ← Protocols for every seam
│       ├── ingestion/
│       │   ├── cloner.py
│       │   ├── detector.py
│       │   ├── walker.py
│       │   └── service.py
│       ├── intelligence/
│       │   ├── parser.py
│       │   ├── languages.py
│       │   ├── symbols.py
│       │   └── imports.py
│       ├── graph/
│       │   ├── schema.py
│       │   ├── backend.py             ← Protocol
│       │   ├── networkx_backend.py
│       │   ├── builder.py
│       │   └── queries.py
│       ├── retrieval/                 ← INTERFACE-DEFINED
│       │   ├── chunking.py
│       │   ├── embeddings.py
│       │   └── stores/base.py
│       ├── agents/                    ← INTERFACE-DEFINED
│       │   ├── base.py
│       │   ├── registry.py
│       │   └── planner.py
│       ├── detection/
│       │   ├── base.py
│       │   ├── registry.py
│       │   └── rules/
│       │       └── python_rules.py    ← circular import rule, real
│       ├── patching/
│       │   ├── diff.py
│       │   └── applier.py
│       ├── validation/
│       │   ├── pipeline.py
│       │   └── checks/
│       │       └── syntax.py
│       ├── sandbox/
│       │   └── runner.py
│       ├── issues/                    ← INTERFACE-DEFINED
│       │   └── source.py
│       ├── api/
│       │   ├── main.py                ← FastAPI app
│       │   └── routes/
│       │       ├── health.py
│       │       ├── repositories.py
│       │       └── graph.py
│       └── cli.py                     ← Typer entry point
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   └── tiny_repo/                 ← real Python repo with 1 cycle
│   ├── test_ingestion.py
│   ├── test_intelligence.py
│   ├── test_graph.py
│   ├── test_detection.py
│   ├── test_patching.py
│   └── test_validation.py
├── pyproject.toml
├── Makefile
├── .gitignore
├── .pre-commit-config.yaml
├── .github/workflows/ci.yml
└── README.md
```

---

## 6. Data & Control Flow

### 6.1 Ingest → Detect (Phase 1 happy path, IMPLEMENTED)

```
caller
  │
  │  IngestRequest(url=..., branch=...)
  ▼
IngestionService.ingest()
  │
  ├──▶ GitCloner.clone()                      ──▶ snapshot on disk
  ├──▶ EcosystemDetector.detect(snapshot)     ──▶ Ecosystem
  ├──▶ RepositoryWalker.walk(snapshot)        ──▶ Iterable[FileRef]
  │
  ▼
Repository  (in-memory aggregate)
  │
  ▼
IntelligenceService.parse_all(repo)
  ├──▶ TreeSitterParser.parse(file)           ──▶ ParsedFile
  └──▶ SymbolExtractor.extract(parsed)        ──▶ list[Symbol]
  │
  ▼
GraphBuilder.build(repo, symbols, imports)
  │
  ▼
GraphBackend (NetworkXGraphBackend)
  │
  ▼
DetectionRegistry.scan_all(repo, graph)
  │
  └──▶ CircularImportRule.scan() ──▶ list[Finding]
  │
  ▼
caller receives list[Finding]
```

### 6.2 Issue → PR (PLANNED, end-to-end flow defined)

```
GitHub issue #N
  │
  ▼  IssueSource.fetch
Issue
  │
  ▼  PlannerAgent.plan(issue, graph, retrieval) ──▶ Plan(steps=[…])
  │
  ▼  for each step: dispatched to specialist agent
  │     RootCauseAgent ──▶ list[CandidateCause]
  │     PatchGenAgent  ──▶ Patch
  │
  ▼  ValidationPipeline.run(patch)
  │     SyntaxValidator → LintValidator → TestValidator → BuildValidator
  │
  ▼  if PASS:  PatchApplier.commit()
  │  if FAIL:  rollback + feed failure back to PatchGenAgent (max N retries)
  │
  ▼  GitHubAdapter.open_pr(branch, body=patch_explanation)
```

This flow is *defined*. It is not implemented. The Protocols at every arrow are real and present in `core/protocols.py`.

---

## 7. Concurrency Model

- **Ingestion, parsing, graph building** are CPU-bound and trivially parallelizable per file. We use `concurrent.futures.ProcessPoolExecutor` keyed by file. Tree-sitter is GIL-bound but releases the GIL during parse, so a `ThreadPoolExecutor` is also viable; we benchmark and switch.
- **HTTP layer (FastAPI)** is async. Long jobs (ingest, scan) are dispatched to a worker queue (Redis+RQ, planned) and the API returns a job id. Phase 1 runs jobs inline for simplicity; the seam for queue dispatch is `JobRunner` Protocol.
- **Agents** will be coroutines; their state machine is async-native. Tools they call are awaitable, with deadlines.

---

## 8. Persistence Model

| What | Where | Status |
|------|-------|--------|
| Repository snapshots | local FS under `$REPOHEAL_WORKDIR` | IMPLEMENTED |
| Knowledge graph | in-memory NetworkX | IMPLEMENTED |
| Knowledge graph (persistent) | Neo4j or PostgreSQL+pgrouting | PLANNED |
| Embeddings | FAISS file / Qdrant collection | INTERFACE-DEFINED |
| Job state | Postgres | PLANNED |
| Findings, patches, validation runs | Postgres | PLANNED |
| Object storage (logs, artifacts) | S3-compatible | PLANNED |
| Memory / learnings | Postgres + pgvector | PLANNED |

The application is **stateless above the persistence layer**, by design. Restart-safety = job-state-restoration from Postgres + working-tree restoration from `$REPOHEAL_WORKDIR`.

---

## 9. Security Model

| Threat | Control | Status |
|--------|---------|--------|
| Malicious repository code executing on host | Sandbox isolation | subprocess only (IMPL); Docker (PLANNED) |
| Prompt injection via repo files / issue bodies | Untrusted-content tagging in agent inputs | PLANNED — Phase 6 |
| Secret leakage in patches | Pre-PR secret scan (`detect-secrets`) in validation pipeline | PLANNED — Phase 9 |
| Sandbox escape | Drop caps, read-only root, no-network, seccomp profile | PLANNED — Phase 9 |
| Arbitrary command via patch | Patches are *content edits* only; build/test commands come from a manifest, never the LLM | IMPLEMENTED-by-design |
| Rate-limit / cost runaway from LLM | Token budget per job, hard ceiling, circuit breaker | PLANNED — Phase 6 |

The architectural commitment that already eliminates a class of attacks: **the LLM never produces shell commands.** Patches are file content; commands come from a fixed allowlist derived from the detected ecosystem.

---

## 10. Failure & Recovery

Every long-running operation produces a `JobResult` with one of: `SUCCESS`, `PARTIAL`, `FAILED`, `TIMEOUT`. There is no silent failure path — `partial` results are first-class and carry a list of `JobError` records.

Specific failure modes addressed:

- **Tree-sitter grammar missing for a language** → file is recorded with `parse_status=UNSUPPORTED`, ingestion continues.
- **Git clone timeout** → retried with exponential backoff (1s, 4s, 16s) up to 3 attempts, then `FAILED`.
- **Validator crashes** → its result is `ERROR`, treated as `FAIL` (fail-closed).
- **Patch application conflict** → rollback to snapshot, finding tagged `unfixable_in_isolation`.
- **Agent infinite loop** → hard step-count and wallclock budget per agent; planner monitors.
- **Hallucinated edits** (LLM proposes change to a file/symbol that doesn't exist) → caught at patch-validation step *before* application, rejected with structured error fed back to the agent.

---

## 11. Scalability Strategy

**Phase 1 target:** repos up to 50k files, 5M LOC, single-node, in-memory graph. This covers OpenJarvis-class repos comfortably.

**Phase 14 target:** monorepos with 1M+ files (Chromium-class).

Path:
1. **Sharded ingestion** by top-level directory.
2. **Persistent graph backend** (Neo4j or Postgres+pgrouting). Same Protocol, swap at DI.
3. **Incremental indexing** keyed on `git diff` since last index. Already designed into the schema (every node has a `last_seen_commit`).
4. **Retrieval index sharding** per language or per service.
5. **Worker pool** for parallel agent execution.

The **incremental-indexing key** is already in `Node`/`Edge` schemas now, even though we don't use it in Phase 1, because adding it later is a schema migration we want to avoid.

---

## 12. Testing Strategy

- **Unit tests** for every pure component. Phase 1 ships with tests for ingestion, intelligence (Python), graph, detection (circular imports), patching, validation.
- **Property-based tests** (Hypothesis) for graph invariants (every `CALLS` target exists, no edge into a deleted node) — defined as fixtures, expanded in Phase 2.
- **Fixture repository** (`tests/fixtures/tiny_repo/`) — a real Python package with a deliberately introduced circular import. End-to-end test runs the full pipeline against it.
- **Integration tests** (PLANNED) — full ingest → scan → patch → validate flow with a real LLM (gated behind `RUN_LLM_TESTS=1`).

---

## 13. Deployment Topology (target)

```
                  ┌────────────────┐
                  │   Load Balancer│
                  └────────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌──────┐     ┌──────┐     ┌──────┐
         │ API  │     │ API  │     │ API  │     (FastAPI replicas, stateless)
         └──┬───┘     └──┬───┘     └──┬───┘
            └─────┬──────┴──────┬─────┘
                  │             │
              ┌───▼────┐    ┌───▼──────┐
              │ Redis  │    │ Postgres │
              │ (queue)│    │ (state)  │
              └───┬────┘    └──────────┘
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
   ┌────────┐ ┌────────┐ ┌────────┐
   │ Worker │ │ Worker │ │ Worker │   (RQ workers, run agents)
   └───┬────┘ └────────┘ └────────┘
       │
       ▼
   ┌─────────────────┐
   │  Docker sandbox │   (per-job ephemeral container)
   └─────────────────┘
```

Phase 1 deployment is `uvicorn repoheal.api.main:app` on one box. The architecture above is the destination, not the starting point.

---

## 14. Why these choices, in one paragraph each

**NetworkX before Neo4j.** Operational complexity of Neo4j (a separate stateful service) is not justified before we have proven the graph queries are correct. The Protocol seam (`GraphBackend`) costs us nothing and lets us swap when the graph hits ~1M nodes. ADR-0002.

**Tree-sitter over native AST modules.** A uniform parsing API across 11 languages is worth more than the marginal accuracy of `ast` for Python. Native modules can be plugged in per-language behind the same `LanguageDefinition` if we ever need them. ADR-0001.

**Pydantic v2 for domain models.** `dataclasses` would be lighter, but we cross a JSON boundary at the API layer; Pydantic gives us free serialization, validation, and OpenAPI schema generation, all of which we'd reimplement otherwise.

**FastAPI over Flask/Django.** Async-native. Auto OpenAPI. Pydantic-integrated. The conventional choice and the right one.

**Custom orchestration over LangGraph for agents.** LangGraph is good but ties us to its execution model and opinions on state. Our durable-execution requirement is specific (Postgres, idempotent steps, replay) and a 200-line custom asyncio state machine is more honest than wrapping LangGraph. We will revisit if the custom code starts looking like a worse LangGraph.

**Hybrid retrieval, not vector-only.** Code search is the worst-case scenario for pure semantic search: identifiers don't paraphrase well, and exact matches matter. ADR-0003.

---

## 15. What this document is *not*

It is not a marketing document. Every claim it makes about implementation status maps to a file in this repo or an issue in `ROADMAP.md`. Where it says **PLANNED**, no code exists. Where it says **INTERFACE-DEFINED**, a `Protocol` exists with no concrete implementation. Where it says **IMPLEMENTED**, there is real code with tests.

That distinction is the entire reason this document exists.
