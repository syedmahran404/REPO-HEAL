# ADR-0006 — Heuristic call-graph construction for Python; deeper analysis is opt-in

- **Status:** Accepted
- **Date:** 2026-05-23 (Phase 2)
- **Decision drivers:** scope, correctness, agility for non-Python languages later.

## Context

Several Phase 2 features (dead-code detection, root-cause walks, real impact analysis) need a call graph: edges of kind `CALLS` between callable nodes. Producing a call graph for Python "correctly" is a research problem (dynamic dispatch, decorators, monkeypatching, `getattr`-style indirection). We do not have the engineering budget to be correct; we have the budget to be *useful*.

## Decision

Build a **heuristic, conservative** call-graph extractor:

1. Walk the tree-sitter AST of every Python module.
2. For each `call` node, record the textual callee (`foo`, `obj.bar`, `pkg.mod.baz`).
3. Resolve callees against the *known symbol table*: the union of all `Symbol` records produced by Phase 1, indexed by qualified name and by simple name within the importing scope.
4. If exactly one symbol matches in scope, emit a `CALLS` edge.
5. If zero match, the call is external — drop it.
6. If multiple match, drop the edge and tag the call with `ambiguous=True` in metadata for future LLM-backed disambiguation.

Inheritance is tractable: `class Foo(Bar):` resolves `Bar` against the symbol table the same way and emits an `INHERITS` edge.

References at module load time (`x = Foo`, `decorator_call(Foo)`) emit `REFERENCES` edges using the same identifier resolution logic.

## Consequences

**Positive:**
- We get usable call/inheritance/reference edges for >80% of typical Python code.
- The heuristic is fully explained in <300 LoC.
- It is fast (single AST pass per file, dictionary lookups for resolution).
- False positives are rare because we drop ambiguous calls.

**Negative:**
- We will miss calls that go through dynamic dispatch (`getattr(obj, name)()`), method resolution across multiple base classes when both define the same name, and calls through `functools.partial` / decorators that rename functions.
- Detection rules built on top must accept some false negatives.

## Future evolution

When budget allows: add a second-pass type-aware resolver using `pyright`'s indexer or `jedi`. Both are designed for incremental analysis and integrate behind the existing `ImportResolver`-style Protocol. The call graph builder will then prefer the type-aware resolver and fall back to the heuristic.

For non-Python languages we will pick per-language tools (e.g. `gopls` for Go). The graph builder is language-agnostic; what changes is the per-language extractor.

## Alternatives rejected

- **`pyan` or `pycallgraph`**: project unmaintained, accuracy poor for modern Python.
- **`pyright --outputjson`**: heavy; requires installing pyright as a runtime dep; we want a pure-Python default that always works.
- **Skip call graph until LLM-backed reasoning ships**: would block dead-code detection and root-cause walks for an unknown amount of time.
