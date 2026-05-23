# ADR-0004 — Build a custom asyncio orchestration runtime instead of adopting LangGraph

- **Status:** Accepted
- **Date:** 2026-05-23 (Phase 2)
- **Decision drivers:** durable-execution requirements, control over persistence, observability granularity, refusal to take a heavy upstream dependency on a moving target.

## Context

The agent layer needs to run a DAG of agent steps, with retries, timeouts, budgets, idempotent replay, and durable state. The leading off-the-shelf option is LangGraph; the alternative is a small custom runtime.

LangGraph gives us:
- Pre-built graph/state primitives.
- A community of recipes.
- Integration with LangChain tooling we are not using.

It costs us:
- An opinionated state model (TypedDict-based) that drifts from our Pydantic-typed domain.
- A persistence model (`Checkpoint` + checkpointer adapters) that does not match our preferred shape (immutable per-attempt records keyed on a deterministic `run_id`).
- A heavy transitive dependency tree (LangChain, langchain-core, etc.) that pulls in things we explicitly do not want — chat-message classes, tool abstractions, prompt templates — none of which we use.
- A moving API surface; LangGraph is still pre-1.0 and breaking changes are common.

## Decision

**Implement a small custom runtime in `src/repoheal/agents/orchestrator.py`.** Keep the surface small enough (≈400 LoC) that it can be re-read in one sitting. Make every Protocol seam swappable so that, the day LangGraph stabilises and we want to migrate, we can do so without rewriting agents themselves.

### Concrete shape

- `ExecutionDAG` — typed adjacency list of agent step ids.
- `MemoryBus` — typed key/value store passed into every `Agent.run()` call. Backed by `dict` for tests, file-on-disk for dev, Postgres-shaped Protocol for production (planned).
- `AgentRunner` — runs *one* step with retry/timeout/wallclock budget; emits structured telemetry; persists per-attempt records.
- `Orchestrator` — topologically schedules steps, fans out independent ones via `asyncio.gather`, halts on first non-recoverable failure, persists state after every step.
- Idempotency: every step has a deterministic `run_id`; a re-run with the same id replays from the persisted state.

## Consequences

**Positive:**
- Total understanding. Every line of orchestration is in our repo and our test suite.
- Persistence model matches the domain (Pydantic-typed records).
- Zero risk of upstream breakage.
- Telemetry is first-class, not bolted on.

**Negative:**
- We own bug-fixing and feature work that LangGraph would otherwise provide.
- Recruits familiar with LangGraph have a small learning curve.
- We forfeit any future LangGraph-specific tools.

## Migration plan if priorities change

The runtime is behind three Protocols (`Agent`, `Orchestrator`, `MemoryBus`). Migrating to LangGraph would mean writing one adapter file that exposes the same Protocol surface backed by LangGraph internals. Estimate: one engineer-week.

## Alternatives rejected

- **LangGraph as the runtime.** Discussed above.
- **Temporal / Inngest / similar durable workflow engines.** Operational cost (a separate stateful service) is not justified at our scale.
- **Plain `asyncio.gather` with no state machine.** No retry/replay → fails the durable-execution requirement.
