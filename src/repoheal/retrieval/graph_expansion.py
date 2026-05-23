"""Stage 3 of retrieval: graph-aware expansion.

Once stages 1 (BM25) and 2 (vector) have produced a fused candidate
list, the chunks they returned are nearly always the *symptom* of a
question, not the *cause*. The graph encodes causal relationships —
"A calls B", "X imports Y" — and we use it here to admit one-hop
structural neighbours that the literal retriever wouldn't find.

The expansion is bounded by:

* ``hops`` — how far to walk (default 1; we rarely benefit from more).
* ``max_added`` — how many extra chunks at most.
* a per-edge-kind allowlist — by default we walk ``CALLS`` and
  ``IMPORTS``; ``INHERITS`` and ``REFERENCES`` are opt-in because they
  often add too many neighbours.

Returns *additional* chunk ids — the caller fuses them in.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from ..core.protocols import GraphBackend
from ..graph.schema import EdgeKind, NodeKind, module_node_id, symbol_node_id


_DEFAULT_KINDS: tuple[str, ...] = (
    EdgeKind.CALLS.value,
    EdgeKind.IMPORTS.value,
)


class GraphExpansion:
    """Expand a chunk-id seed set with structural neighbours."""

    def __init__(
        self,
        graph: GraphBackend,
        chunk_index: "ChunkGraphIndex",
        *,
        kinds: tuple[str, ...] = _DEFAULT_KINDS,
    ) -> None:
        self._graph = graph
        self._chunks = chunk_index
        self._kinds = kinds

    def expand(
        self,
        seed_chunk_ids: Iterable[str],
        *,
        hops: int = 1,
        max_added: int = 10,
    ) -> list[str]:
        if hops <= 0 or max_added <= 0:
            return []

        seeds = list(seed_chunk_ids)
        if not seeds:
            return []

        # Map seed chunks -> graph nodes.
        seed_nodes: list[str] = []
        for cid in seeds:
            node_id = self._chunks.node_id_for_chunk(cid)
            if node_id is not None:
                seed_nodes.append(node_id)

        if not seed_nodes:
            return []

        # BFS up to `hops`, collecting nodes along the way.
        seen_nodes: set[str] = set(seed_nodes)
        # (node, depth)
        queue: deque[tuple[str, int]] = deque((n, 0) for n in seed_nodes)
        out_nodes: list[str] = []

        while queue and len(out_nodes) < max_added * 2:
            node, depth = queue.popleft()
            if depth >= hops:
                continue
            for kind in self._kinds:
                for nb in self._safe_neighbors(node, kind):
                    if nb in seen_nodes:
                        continue
                    seen_nodes.add(nb)
                    out_nodes.append(nb)
                    queue.append((nb, depth + 1))

        # Map graph nodes -> chunk ids; deduplicate against the seeds.
        seed_set = set(seeds)
        added: list[str] = []
        for node in out_nodes:
            for cid in self._chunks.chunks_for_node(node):
                if cid in seed_set or cid in added:
                    continue
                added.append(cid)
                if len(added) >= max_added:
                    return added
        return added

    def _safe_neighbors(self, node: str, kind: str) -> list[str]:
        try:
            return list(self._graph.neighbors(node, kind=kind, direction="both"))
        except Exception:
            return []


class ChunkGraphIndex:
    """Bidirectional map between chunk ids and graph nodes.

    Built once after the graph is populated and the chunker has run.
    Keeps both directions because graph expansion needs chunk → node
    and the inverse to pick up extra chunks attached to a discovered
    neighbour.
    """

    def __init__(self) -> None:
        self._chunk_to_node: dict[str, str] = {}
        self._node_to_chunks: dict[str, list[str]] = {}

    def add(self, chunk_id: str, node_id: str | None) -> None:
        if node_id is None:
            return
        self._chunk_to_node[chunk_id] = node_id
        self._node_to_chunks.setdefault(node_id, []).append(chunk_id)

    def node_id_for_chunk(self, chunk_id: str) -> str | None:
        return self._chunk_to_node.get(chunk_id)

    def chunks_for_node(self, node_id: str) -> list[str]:
        return list(self._node_to_chunks.get(node_id, ()))

    @classmethod
    def build(
        cls,
        chunks: Iterable["Chunk"],  # noqa: F821 — forward ref to avoid cycle
        graph: GraphBackend,
    ) -> "ChunkGraphIndex":
        """Build by inspecting each chunk's symbol_qname and resolving
        it into a graph node id. Module preamble / whole-file chunks
        attach to the file's module node."""
        idx = cls()
        for chunk in chunks:
            node_id = _node_id_for_chunk(chunk, graph)
            idx.add(chunk.id, node_id)
        return idx


def _node_id_for_chunk(chunk, graph: GraphBackend) -> str | None:
    """Best-effort node id for a chunk."""
    if chunk.symbol_qname:
        # Try function then method then class.
        for kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
            node_id = symbol_node_id(chunk.symbol_qname, kind=kind)
            if graph.has_node(node_id):
                return node_id
    # Fallback: module node based on file path.
    module_qname = _module_qname_from_path(chunk.file_path)
    module_id = module_node_id(module_qname)
    if graph.has_node(module_id):
        return module_id
    return None


def _module_qname_from_path(path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else path.stem


__all__ = ["ChunkGraphIndex", "GraphExpansion"]
