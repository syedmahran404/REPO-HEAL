"""NetworkX-backed implementation of :class:`GraphBackend`.

In-process, in-memory ``MultiDiGraph``. Suitable for repos up to ~1M
nodes on a single machine; see ADR-0002 for the migration path to
Neo4j when we outgrow it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import networkx as nx

from ..exceptions import NodeNotFoundError


class NetworkXGraphBackend:
    """A :class:`~repoheal.core.protocols.GraphBackend` over NetworkX."""

    def __init__(self) -> None:
        self._g: nx.MultiDiGraph = nx.MultiDiGraph()

    # -- mutation -------------------------------------------------------

    def add_node(self, node_id: str, /, **attrs: Any) -> None:
        self._g.add_node(node_id, **attrs)

    def add_edge(self, src: str, dst: str, /, *, kind: str, **attrs: Any) -> None:
        # Ensure endpoints exist; we want graph integrity to be loud.
        if not self._g.has_node(src):
            self._g.add_node(src)
        if not self._g.has_node(dst):
            self._g.add_node(dst)
        self._g.add_edge(src, dst, key=kind, kind=kind, **attrs)

    def has_node(self, node_id: str) -> bool:
        return self._g.has_node(node_id)

    # -- read -----------------------------------------------------------

    def neighbors(
        self,
        node_id: str,
        *,
        kind: str | None = None,
        direction: str = "out",
    ) -> Iterable[str]:
        if not self._g.has_node(node_id):
            raise NodeNotFoundError(node_id)

        if direction == "out":
            view = self._g.out_edges(node_id, keys=True, data=True)
            return [v for _, v, k, _ in view if kind is None or k == kind]
        if direction == "in":
            view = self._g.in_edges(node_id, keys=True, data=True)
            return [u for u, _, k, _ in view if kind is None or k == kind]
        if direction == "both":
            outs = list(self.neighbors(node_id, kind=kind, direction="out"))
            ins = list(self.neighbors(node_id, kind=kind, direction="in"))
            return outs + ins
        raise ValueError(f"direction must be 'out', 'in', or 'both', got {direction!r}")

    def node_attrs(self, node_id: str) -> dict[str, Any]:
        if not self._g.has_node(node_id):
            raise NodeNotFoundError(node_id)
        return dict(self._g.nodes[node_id])

    def all_nodes(self) -> Iterable[str]:
        return list(self._g.nodes)

    def find_cycles(self, *, kind: str | None = None) -> list[list[str]]:
        """Return strongly-connected components of size > 1, optionally
        restricted to a single edge kind. Each result is the cycle's
        node list.

        We use SCCs rather than ``simple_cycles`` because:

        * ``simple_cycles`` is exponential in the worst case;
        * for "is there a cycle here?" the SCC answer is sufficient and
          near-linear.
        """
        view: nx.DiGraph
        if kind is None:
            view = nx.DiGraph(self._g)
        else:
            edges = [
                (u, v)
                for u, v, k in self._g.edges(keys=True)
                if k == kind
            ]
            view = nx.DiGraph()
            view.add_nodes_from(self._g.nodes)
            view.add_edges_from(edges)

        cycles: list[list[str]] = []
        for component in nx.strongly_connected_components(view):
            if len(component) > 1:
                cycles.append(sorted(component))
            elif len(component) == 1:
                # Self-loop is also a cycle.
                node = next(iter(component))
                if view.has_edge(node, node):
                    cycles.append([node])
        return cycles

    # -- bulk -----------------------------------------------------------

    def node_count(self) -> int:
        return self._g.number_of_nodes()

    def edge_count(self) -> int:
        return self._g.number_of_edges()

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable representation. Stable enough for snapshots."""
        return {
            "nodes": [
                {"id": n, **self._g.nodes[n]}
                for n in sorted(self._g.nodes)
            ],
            "edges": [
                {"src": u, "dst": v, "kind": k, **{kk: vv for kk, vv in d.items() if kk != "kind"}}
                for u, v, k, d in self._g.edges(keys=True, data=True)
            ],
        }
