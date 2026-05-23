"""RootCauseAgent — the first real, non-LLM agent.

Given an "anchor" location in the graph (a node id or a qualified name),
walks the graph upstream along ``CALLS``, ``REFERENCES``, and ``IMPORTS``
edges and emits ranked :class:`RootCauseHypothesis` records.

Why this is genuinely useful even without an LLM:

* Most production failures arrive with a stack frame or a finding that
  pinpoints the *symptom*. The root cause is, on average, 1–3 hops
  upstream in the call graph.
* Walking the graph up from the symptom and ranking by graph distance
  produces a short list of candidates a human reviewer can triage in
  a minute. That's a real-world workflow.
* The ranking gets sharper when the retrieval service is available:
  candidates whose source is similar to the anchor's source bubble up
  (often the same buggy idiom in two places).

The agent is also the canonical "this is what an Agent looks like in
our runtime" example. LLM-backed agents land on the same Protocol,
read the same state dict, return the same :class:`AgentResult`.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..core.protocols import GraphBackend
from ..graph.schema import EdgeKind, NodeKind
from ..logging import get_logger
from .base import AgentResult, AgentStatus

_log = get_logger(__name__)


# Edges that are interesting for root-cause reasoning. We walk *backward*
# along these (i.e. find predecessors). The order matters for tie-breaks:
# a CALLS predecessor is more relevant than a REFERENCES predecessor.
_UPSTREAM_KINDS: tuple[str, ...] = (
    EdgeKind.CALLS.value,
    EdgeKind.REFERENCES.value,
    EdgeKind.IMPORTS.value,
)


class RootCauseHypothesis(BaseModel):
    """One candidate explanation for an anchor node's failure."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    qualified_name: str
    file: Path | None = None
    score: float
    distance: int
    rationale: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RootCauseAgent:
    """Walk the graph upstream and rank candidates."""

    @property
    def name(self) -> str:
        return "root_cause"

    async def run(self, state: dict[str, Any]) -> AgentResult:
        started = datetime.now(timezone.utc)

        graph: GraphBackend | None = state.get("graph")
        retrieval = state.get("retrieval")  # RetrievalService | None
        inputs: dict[str, Any] = state.get("inputs", {}) or {}

        anchor = inputs.get("anchor_node")
        if not isinstance(anchor, str):
            return _failed(
                self.name, started, "missing or invalid 'anchor_node' input"
            )

        if graph is None:
            return _failed(self.name, started, "no graph in state")

        if not graph.has_node(anchor):
            return _failed(
                self.name,
                started,
                f"anchor node not found in graph: {anchor!r}",
            )

        max_depth = int(inputs.get("max_depth", 3))
        top_k = int(inputs.get("top_k", 5))

        candidates = self._bfs_upstream(graph, anchor, max_depth=max_depth)
        if not candidates:
            return AgentResult(
                agent=self.name,
                status=AgentStatus.OK,
                summary="no upstream candidates found",
                output={"hypotheses": [], "anchor_node": anchor},
                started_at=started,
                finished_at=datetime.now(timezone.utc),
            )

        ranked = self._score(graph, retrieval, anchor, candidates, top_k=top_k)

        return AgentResult(
            agent=self.name,
            status=AgentStatus.OK,
            summary=f"{len(ranked)} hypothesis/es ranked from {len(candidates)} candidates",
            output={
                "hypotheses": [h.model_dump(mode="json") for h in ranked],
                "anchor_node": anchor,
                "max_depth": max_depth,
                "top_k": top_k,
            },
            started_at=started,
            finished_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------

    def _bfs_upstream(
        self,
        graph: GraphBackend,
        anchor: str,
        *,
        max_depth: int,
    ) -> dict[str, int]:
        """Return a map of candidate_node_id → minimum graph distance from
        the anchor along the configured edge kinds (walking backward)."""
        distance: dict[str, int] = {anchor: 0}
        queue: deque[tuple[str, int]] = deque([(anchor, 0)])

        while queue:
            node, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for kind in _UPSTREAM_KINDS:
                try:
                    preds = list(graph.neighbors(node, kind=kind, direction="in"))
                except Exception:
                    continue
                for pred in preds:
                    if pred in distance:
                        continue
                    distance[pred] = depth + 1
                    queue.append((pred, depth + 1))

        # Drop the anchor itself.
        distance.pop(anchor, None)
        return distance

    def _score(
        self,
        graph: GraphBackend,
        retrieval: Any,
        anchor: str,
        candidates: dict[str, int],
        *,
        top_k: int,
    ) -> list[RootCauseHypothesis]:
        """Combine graph distance with retrieval similarity.

        Distance score: ``1 / (1 + d)``.
        Retrieval score: ``1`` if the candidate's chunk shows up in
        retrieval's top-k for the anchor's source, else 0. (Optional;
        retrieval may be absent.)
        Composite: ``0.7*distance + 0.3*retrieval``.
        """
        retrieval_hits: set[str] = set()
        if retrieval is not None:
            anchor_text = self._anchor_text(graph, anchor)
            if anchor_text:
                try:
                    res = retrieval.search(anchor_text, top_k=max(top_k * 2, 8))
                    for chunk in res.chunks:
                        if chunk.symbol_qname:
                            retrieval_hits.add(chunk.symbol_qname)
                except Exception as exc:
                    _log.warning("root_cause.retrieval_failed", error=str(exc))

        out: list[RootCauseHypothesis] = []
        for node_id, distance in candidates.items():
            attrs = graph.node_attrs(node_id)
            kind = attrs.get("kind")
            if kind not in (
                NodeKind.FUNCTION.value,
                NodeKind.METHOD.value,
                NodeKind.CLASS.value,
                NodeKind.MODULE.value,
            ):
                # We only surface hypothes-ised callables and modules.
                continue
            qname = attrs.get("qualified_name") or node_id
            file_attr = attrs.get("file")
            file_path: Path | None = (
                Path(file_attr) if isinstance(file_attr, str) else None
            )

            distance_score = 1.0 / (1.0 + distance)
            retrieval_score = 1.0 if qname in retrieval_hits else 0.0
            composite = 0.7 * distance_score + 0.3 * retrieval_score

            rationale_parts = [f"upstream from anchor by {distance} hop(s)"]
            if retrieval_score > 0.0:
                rationale_parts.append(
                    "code is similar to the anchor (retrieval hit)"
                )
            rationale = "; ".join(rationale_parts)

            out.append(
                RootCauseHypothesis(
                    node_id=node_id,
                    qualified_name=qname,
                    file=file_path,
                    score=round(composite, 4),
                    distance=distance,
                    rationale=rationale,
                    metadata={
                        "kind": kind,
                        "distance_score": round(distance_score, 4),
                        "retrieval_score": retrieval_score,
                    },
                )
            )

        out.sort(key=lambda h: (-h.score, h.distance, h.qualified_name))
        return out[:top_k]

    def _anchor_text(self, graph: GraphBackend, anchor: str) -> str:
        """Build a textual representation of the anchor for retrieval.

        We use the qualified name + a short summary of the node's
        attributes. We could read source from disk, but that requires
        path resolution we don't always have in the agent context.
        """
        attrs = graph.node_attrs(anchor)
        qname = attrs.get("qualified_name") or anchor
        kind = attrs.get("kind") or ""
        return f"{kind} {qname}"


# --- helpers ----------------------------------------------------------------


def _failed(agent_name: str, started: datetime, msg: str) -> AgentResult:
    return AgentResult(
        agent=agent_name,
        status=AgentStatus.FAILED,
        summary=msg,
        errors=(msg,),
        started_at=started,
        finished_at=datetime.now(timezone.utc),
    )


__all__ = ["RootCauseAgent", "RootCauseHypothesis"]
