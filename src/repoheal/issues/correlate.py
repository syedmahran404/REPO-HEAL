"""Issue correlator — enrich an :class:`Issue` with code anchors.

Given an Issue and a repository's graph + retrieval index, this builds:

* every traceback found in the issue body and any of its comments, each
  resolved to graph nodes via :class:`TracebackCorrelator`;
* a list of candidate files surfaced by :class:`RetrievalService`
  using the issue's title + body as a query — these are the files
  most likely to contain the relevant code, even when the issue
  doesn't include a stack trace.

The output is consumed by:

* the planner (Phase 6) to decide which agents to dispatch;
* the :class:`RootCauseAgent` to anchor on a concrete graph node;
* the patch-generation agent (planned) to bound the search space for
  proposed edits.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core.models import Chunk, Issue, Repository
from ..core.protocols import GraphBackend
from ..logging import get_logger
from ..retrieval import RetrievalService
from ..runtime import (
    CorrelatedTraceback,
    ParsedTraceback,
    TracebackCorrelator,
    TracebackParser,
)

_log = get_logger(__name__)


class IssueCorrelation(BaseModel):
    """An issue plus everything we figured out about its code anchors."""

    model_config = ConfigDict(frozen=True)

    issue: Issue
    correlated_tracebacks: tuple[CorrelatedTraceback, ...] = ()
    candidate_files: tuple[Path, ...] = ()
    candidate_chunks: tuple[Chunk, ...] = ()

    @property
    def primary_anchor_node(self) -> str | None:
        """Best graph anchor across all tracebacks (first traceback's
        primary frame), or ``None``."""
        for tb in self.correlated_tracebacks:
            primary = tb.primary_frame
            if primary is not None and primary.node_id is not None:
                return primary.node_id
        return None


class IssueCorrelator:
    """Combines traceback parsing + correlation + retrieval for one issue."""

    _QUERY_MAX_CHARS = 4000  # generous; enough for title + body
    _RETRIEVAL_TOP_K = 10

    def __init__(
        self,
        repo: Repository,
        graph: GraphBackend,
        *,
        retrieval: RetrievalService | None = None,
        parser: TracebackParser | None = None,
    ) -> None:
        self._repo = repo
        self._graph = graph
        self._retrieval = retrieval
        self._parser = parser or TracebackParser()
        self._correlator = TracebackCorrelator(repo, graph)

    # ------------------------------------------------------------------

    def correlate(self, issue: Issue) -> IssueCorrelation:
        texts = list(_iter_issue_texts(issue))

        correlated_tbs: list[CorrelatedTraceback] = []
        for text in texts:
            parsed = self._parser.parse(text)
            if not parsed.frames:
                continue
            correlated_tbs.append(self._correlator.correlate(parsed))

        candidate_files: list[Path] = []
        candidate_chunks: list[Chunk] = []
        if self._retrieval is not None:
            query = self._build_query(issue)
            try:
                result = self._retrieval.search(query, top_k=self._RETRIEVAL_TOP_K)
                for chunk in result.chunks:
                    candidate_chunks.append(chunk)
                    if chunk.file_path not in candidate_files:
                        candidate_files.append(chunk.file_path)
            except Exception as exc:
                _log.warning(
                    "issues.retrieval_failed",
                    issue_number=str(issue.number),
                    error=str(exc),
                )

        # Files referenced by tracebacks are also candidates — they live
        # *first* because a traceback is the strongest hint we have.
        for tb in correlated_tbs:
            for f in tb.repo_files:
                if f not in candidate_files:
                    candidate_files.insert(0, f)

        return IssueCorrelation(
            issue=issue,
            correlated_tracebacks=tuple(correlated_tbs),
            candidate_files=tuple(candidate_files),
            candidate_chunks=tuple(candidate_chunks),
        )

    # ------------------------------------------------------------------

    def _build_query(self, issue: Issue) -> str:
        parts = [issue.title, issue.body]
        # Comment bodies aren't included in the retrieval query because
        # they often contain unrelated discussion; the body alone is
        # the highest-signal text.
        joined = "\n\n".join(p for p in parts if p)
        return joined[: self._QUERY_MAX_CHARS]


# --- helpers --------------------------------------------------------------


def _iter_issue_texts(issue: Issue) -> Iterable[str]:
    """Yield every block of text we should scan for tracebacks: the body
    and each comment body in metadata['comments']."""
    if issue.body:
        yield issue.body
    comments = issue.metadata.get("comments")
    if isinstance(comments, list):
        for c in comments:
            if isinstance(c, dict):
                body = c.get("body")
                if isinstance(body, str) and body:
                    yield body


__all__ = ["IssueCorrelation", "IssueCorrelator"]
