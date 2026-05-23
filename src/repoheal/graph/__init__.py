"""Knowledge graph subsystem.

* :mod:`schema` — node/edge type enums and helpers.
* :class:`NetworkXGraphBackend` — first concrete backend.
* :class:`GraphBuilder` — turn parsed files into nodes & edges.
* :mod:`queries` — high-level graph analyses (impact analysis, cycles).
"""

from .builder import GraphBuilder
from .networkx_backend import NetworkXGraphBackend
from .queries import ImpactAnalysis
from .schema import EdgeKind, NodeKind, file_node_id, module_node_id, symbol_node_id

__all__ = [
    "EdgeKind",
    "GraphBuilder",
    "ImpactAnalysis",
    "NetworkXGraphBackend",
    "NodeKind",
    "file_node_id",
    "module_node_id",
    "symbol_node_id",
]
