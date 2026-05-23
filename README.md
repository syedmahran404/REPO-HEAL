# REPO-HEAL

> Autonomous Repository Analysis & Self-Healing AI Engine.

REPO-HEAL ingests a repository, builds a deep semantic understanding of it (parsing, knowledge graph, retrieval), detects defects, generates safe fixes, validates them in a sandbox, and opens a pull request — autonomously.

This repository is in **active early-phase construction**. The architecture is finalized; implementation is shipping in vertical slices.

- **What is real today:** see [`docs/ROADMAP.md`](docs/ROADMAP.md).
- **System design:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- **Hard architectural decisions:** [`docs/decisions/`](docs/decisions/).

## Phase 1 — what works right now

- Repository ingestion (local path or git clone).
- Ecosystem detection across 11 languages.
- Tree-sitter based parsing, with full Python symbol extraction.
- NetworkX-backed knowledge graph with impact-analysis and cycle detection.
- One end-to-end bug-detection rule: **circular imports**.
- Unified-diff patch generation with safe apply / rollback.
- Validation pipeline (syntax validator implemented).
- Subprocess sandbox runner.
- FastAPI service + Typer CLI exposing all of the above.
- Unit tests across every implemented subsystem.

## Install

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
```

## Use it (CLI)

```bash
# Ingest a local repository, print its ecosystem and file count
repoheal ingest --path ./tests/fixtures/tiny_repo

# Build the knowledge graph and emit it as JSON
repoheal graph build --path ./tests/fixtures/tiny_repo --out graph.json

# Scan for circular imports
repoheal scan --path ./tests/fixtures/tiny_repo --rule circular_imports
```

## Use it (HTTP API)

```bash
uvicorn repoheal.api.main:app --reload
# then
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/repositories/ingest \
    -H 'content-type: application/json' \
    -d '{"path": "./tests/fixtures/tiny_repo"}'
```

OpenAPI: `http://localhost:8000/docs`.

## Develop

```bash
make dev          # install with dev deps
make check        # lint + typecheck + test
make api          # run the FastAPI service
```

## License

Apache-2.0.
