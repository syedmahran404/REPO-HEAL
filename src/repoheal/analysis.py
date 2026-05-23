"""High-level analysis facade.

Wires together ingestion → parsing → graph build → detection → retrieval
in a single, reusable entry point. Both the FastAPI handlers and the
Typer CLI sit on top of this; tests can use it directly.

This is the *only* place where all the subsystems are composed at
once. Everything below this in the dependency tree is single-purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .core.models import Finding, ParsedFile, Repository
from .core.protocols import GraphBackend
from .detection import RuleRegistry, default_registry as default_rule_registry
from .graph import GraphBuilder, NetworkXGraphBackend
from .ingestion import IngestionService
from .intelligence import TreeSitterParser
from .logging import get_logger
from .obs.tracing import traced
from .retrieval import RetrievalService

_log = get_logger(__name__)


@dataclass
class AnalysisResult:
    """Outcome of a full analyze() run."""

    repository: Repository
    parsed_files: list[ParsedFile]
    graph: GraphBackend
    findings: list[Finding] = field(default_factory=list)
    retrieval: RetrievalService | None = None

    def graph_stats(self) -> dict[str, int]:
        return {
            "nodes": self.graph.node_count(),
            "edges": self.graph.edge_count(),
        }


class AnalysisService:
    """Top-level analysis orchestrator."""

    def __init__(
        self,
        *,
        ingestion: IngestionService | None = None,
        parser: TreeSitterParser | None = None,
        builder: GraphBuilder | None = None,
        rules: RuleRegistry | None = None,
        retrieval_factory: "RetrievalFactory | None" = None,
    ) -> None:
        self._ingestion = ingestion or IngestionService()
        self._parser = parser or TreeSitterParser()
        self._builder = builder or GraphBuilder()
        self._rules = rules or default_rule_registry()
        self._retrieval_factory = retrieval_factory or default_retrieval_factory

    # ------------------------------------------------------------------

    def analyze(
        self,
        source: str | Path,
        *,
        branch: str | None = None,
        rule_ids: list[str] | None = None,
        build_retrieval: bool = False,
    ) -> AnalysisResult:
        """Ingest, parse, build the graph, run rules, optionally index for retrieval."""
        with traced("analysis.analyze", source=str(source), branch=branch or ""):
            with traced("analysis.ingest"):
                repo = self._ingestion.ingest(source, branch=branch)

            with traced("analysis.parse_all", file_count=len(repo.files)):
                parsed_files: list[ParsedFile] = []
                for file_ref in repo.files:
                    if file_ref.is_binary:
                        continue
                    if file_ref.size_bytes > _MAX_PARSE_SIZE:
                        continue
                    parsed = self._parser.parse_path(repo.root, file_ref)
                    parsed_files.append(parsed)

            graph: GraphBackend = NetworkXGraphBackend()
            with traced("analysis.graph_build", parsed_files=len(parsed_files)):
                self._builder.build(repo, parsed_files, graph)

            _log.info(
                "analysis.graph_built",
                nodes=graph.node_count(),
                edges=graph.edge_count(),
            )

            with traced("analysis.scan", rules=str(rule_ids) if rule_ids else "all"):
                findings = self._rules.scan(repo, graph, rule_ids=rule_ids)
            _log.info(
                "analysis.scan_done",
                rules_run=len(self._rules.all() if rule_ids is None else rule_ids),
                finding_count=len(findings),
            )

            retrieval: RetrievalService | None = None
            if build_retrieval:
                with traced("analysis.retrieval_index"):
                    retrieval = self._retrieval_factory()
                    indexed = retrieval.index(repo, parsed_files, graph)
                _log.info("analysis.retrieval_indexed", chunks=indexed)

            return AnalysisResult(
                repository=repo,
                parsed_files=parsed_files,
                graph=graph,
                findings=findings,
                retrieval=retrieval,
            )


# --- factory hooks ----------------------------------------------------------


from typing import Callable  # noqa: E402

RetrievalFactory = Callable[[], RetrievalService]


def default_retrieval_factory() -> RetrievalService:
    """Default factory: in-memory store, hash embeddings (ADR-0005)."""
    return RetrievalService()


_MAX_PARSE_SIZE = 2 * 1024 * 1024  # 2 MiB
