"""High-level graph analyses.

These work against any :class:`~repoheal.core.protocols.GraphBackend`,
which is the entire point of the Protocol.
"""

from __future__ import annotations

from collections import deque

from ..core.protocols import GraphBackend


class ImpactAnalysis:
    """Reasoning over the knowledge graph.

    All methods are pure functions of the backend's state; nothing is
    mutated.
    """

    def __init__(self, graph: GraphBackend) -> None:
        self._graph = graph

    # ------------------------------------------------------------------

    def downstream(
        self,
        node_id: str,
        *,
        kind: str | None = None,
        max_depth: int = 5,
    ) -> set[str]:
        """All nodes reachable *out* from ``node_id`` within ``max_depth``.

        "What breaks if this changes?" — this is the answer.
        """
        return self._bfs(node_id, kind=kind, max_depth=max_depth, direction="out")

    def upstream(
        self,
        node_id: str,
        *,
        kind: str | None = None,
        max_depth: int = 5,
    ) -> set[str]:
        """All nodes reachable *in* to ``node_id`` within ``max_depth``.

        "What might be the cause of this failing?" — this is the answer.
        """
        return self._bfs(node_id, kind=kind, max_depth=max_depth, direction="in")

    def cycles_of_kind(self, kind: str) -> list[list[str]]:
        """All cycles using only edges of the given kind."""
        return self._graph.find_cycles(kind=kind)

    # ------------------------------------------------------------------

    def _bfs(
        self,
        start: str,
        *,
        kind: str | None,
        max_depth: int,
        direction: str,
    ) -> set[str]:
        if not self._graph.has_node(start):
            return set()

        visited: set[str] = set()
        # (node, depth)
        queue: deque[tuple[str, int]] = deque([(start, 0)])

        while queue:
            node, depth = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            if depth >= max_depth:
                continue
            for nb in self._graph.neighbors(node, kind=kind, direction=direction):
                if nb not in visited:
                    queue.append((nb, depth + 1))

        visited.discard(start)
        return visited
