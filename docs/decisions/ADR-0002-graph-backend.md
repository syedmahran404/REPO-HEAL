# ADR-0002 — NetworkX in-process graph for Phase 1, Neo4j as the swap target

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decision drivers:** correctness-first, operational simplicity, swap cost.

## Context

The knowledge graph is read by every other subsystem: detection, retrieval, root-cause analysis, agents. It must support fast neighborhood queries, cycle detection, shortest paths, and traversal with edge-type filtering.

Options at Phase 1:

1. **NetworkX** — pure Python, in-memory `MultiDiGraph`. Excellent algorithm coverage. Zero ops. Caps out around ~1M nodes for serious workloads on a single machine.
2. **Neo4j** — production graph DB. Cypher query language. Persistent. Distributed-capable. Operational cost: a stateful service to run, back up, version, and observe. Setup friction non-trivial.
3. **PostgreSQL + recursive CTEs** — convenient if we already have Postgres for other state. Awkward for cycle detection and weighted shortest path.
4. **Custom adjacency-list in Postgres + pgrouting** — fast graph queries; less awkward than raw recursive CTEs; ties graph storage to relational store.

## Decision

**Phase 1: NetworkX, in-process, behind a `GraphBackend` Protocol.**
**Phase ≥ 4: Neo4j, same Protocol, drop-in.**

We are *not* building both at once. We are building one and making the second a known migration with a stable interface.

## Consequences

**Positive:**
- Zero external services in Phase 1. Anyone can run REPO-HEAL with `pip install` and one command.
- Tests are trivially fast (everything in memory).
- All the graph algorithms we need (Tarjan SCC, Dijkstra, BFS) are present and battle-tested.
- The Protocol seam is enforced by mypy: any code path that touches the graph goes through the interface, so the migration to Neo4j is mechanical.

**Negative:**
- NetworkX is single-process. We cannot share the graph between API replicas without serializing it. For Phase 1's single-node deployment, fine; for multi-node, we either move to Neo4j or run a "graph service" that wraps NetworkX.
- Memory blow-up risk. We track this with metrics from day one (`graph_nodes_total`, `graph_edges_total`, `graph_memory_bytes`) so the tipping point is observed, not surprised.

## Migration plan

When the trip-wires fire (`graph_nodes_total > 750k` or `graph_memory_bytes > 4GB`):

1. Stand up Neo4j in dev.
2. Implement `Neo4jGraphBackend(GraphBackend)`. The Protocol's surface is small (15 methods); estimate one engineer-week including Cypher tuning.
3. Switch DI in `config.py`. Run the existing graph tests against the new backend (they target the Protocol, not the implementation).

## Alternatives rejected

- **Building Neo4j first** — premature; we don't yet know the access patterns well enough to design the Cypher queries optimally. Better to learn them via NetworkX and translate.
- **PostgreSQL recursive CTEs** — readability-hostile for the kind of multi-hop queries impact analysis needs.
