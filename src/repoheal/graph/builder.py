"""Graph builder: turn parsed files into graph nodes and edges.

This is a pure transformation. Given a repository, a stream of
:class:`~repoheal.core.models.ParsedFile`, and a
:class:`~repoheal.core.protocols.GraphBackend`, it populates the
backend.

The backend is the only side-effecting component; the builder itself
is data-in / data-out, which makes it straightforward to test against
an in-memory backend and equally easy to swap for Neo4j later.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..core.models import (
    ImportEdge,
    Language,
    ParsedFile,
    Repository,
    Symbol,
    SymbolKind,
)
from ..core.protocols import GraphBackend, ImportResolver as ImportResolverProto
from ..intelligence.imports import PythonImportResolver
from ..logging import get_logger
from .schema import (
    EdgeKind,
    NodeKind,
    file_node_id,
    module_node_id,
    symbol_node_id,
)

_log = get_logger(__name__)


_SYMBOL_KIND_TO_NODE_KIND: dict[SymbolKind, NodeKind] = {
    SymbolKind.MODULE: NodeKind.MODULE,
    SymbolKind.CLASS: NodeKind.CLASS,
    SymbolKind.FUNCTION: NodeKind.FUNCTION,
    SymbolKind.METHOD: NodeKind.METHOD,
    SymbolKind.VARIABLE: NodeKind.VARIABLE,
    SymbolKind.IMPORT: NodeKind.IMPORT,
}


class GraphBuilder:
    """Populate a graph backend from parsed files.

    ``import_resolvers`` maps language to a resolver. Phase 1 wires up
    Python; other languages can be added without touching the builder.
    """

    def __init__(
        self,
        *,
        import_resolvers: dict[Language, ImportResolverProto] | None = None,
    ) -> None:
        self._resolvers: dict[Language, ImportResolverProto] = (
            import_resolvers
            if import_resolvers is not None
            else {Language.PYTHON: PythonImportResolver()}
        )

    # ------------------------------------------------------------------

    def build(
        self,
        repo: Repository,
        parsed_files: Iterable[ParsedFile],
        graph: GraphBackend,
    ) -> None:
        """Add file, module, symbol, contains-, and imports- edges to ``graph``.

        The builder is idempotent on repeated calls with the same input
        (NetworkX add_node/add_edge merge attributes; same edge key with
        same endpoints has no extra effect)."""
        parsed_list = list(parsed_files)

        for parsed in parsed_list:
            self._add_file_and_symbols(parsed, graph)

        for parsed in parsed_list:
            self._add_imports(parsed, repo, graph)

    # ------------------------------------------------------------------

    def _add_file_and_symbols(self, parsed: ParsedFile, graph: GraphBackend) -> None:
        file_id = file_node_id(parsed.file.path)
        graph.add_node(
            file_id,
            kind=NodeKind.FILE.value,
            path=parsed.file.path.as_posix(),
            language=parsed.file.language.value,
            size_bytes=parsed.file.size_bytes,
        )

        # Index symbols by qualified_name so we can wire CONTAINS edges.
        by_qname: dict[str, Symbol] = {s.qualified_name: s for s in parsed.symbols}

        for symbol in parsed.symbols:
            node_kind = _SYMBOL_KIND_TO_NODE_KIND.get(symbol.kind)
            if node_kind is None:
                continue
            sym_id = symbol_node_id(symbol.qualified_name, kind=node_kind)
            graph.add_node(
                sym_id,
                kind=node_kind.value,
                name=symbol.name,
                qualified_name=symbol.qualified_name,
                file=symbol.file.as_posix(),
                start_line=symbol.range.start_line,
                end_line=symbol.range.end_line,
            )

            # File CONTAINS top-level module/class/function.
            if symbol.parent is None or symbol.kind == SymbolKind.MODULE:
                graph.add_edge(file_id, sym_id, kind=EdgeKind.CONTAINS.value)
            else:
                # Symbol's parent is a qualified_name; resolve to its node id
                # if we know what kind it is. A method's parent is a class,
                # a nested function's parent is a function, etc.
                parent_sym = by_qname.get(symbol.parent)
                if parent_sym is not None:
                    parent_kind = _SYMBOL_KIND_TO_NODE_KIND.get(parent_sym.kind)
                    if parent_kind is not None:
                        parent_id = symbol_node_id(
                            parent_sym.qualified_name, kind=parent_kind
                        )
                        graph.add_edge(parent_id, sym_id, kind=EdgeKind.CONTAINS.value)
                else:
                    # Parent is the implicit module.
                    module_id = module_node_id(symbol.parent)
                    if graph.has_node(module_id):
                        graph.add_edge(module_id, sym_id, kind=EdgeKind.CONTAINS.value)

    # ------------------------------------------------------------------

    def _add_imports(
        self,
        parsed: ParsedFile,
        repo: Repository,
        graph: GraphBackend,
    ) -> None:
        resolver = self._resolvers.get(parsed.language)
        if resolver is None:
            return  # languages without a resolver: skip imports edges

        src_module_qname = _module_qname_from_path(parsed.file.path)
        src_module_id = module_node_id(src_module_qname)
        # Ensure the importing module node exists.
        if not graph.has_node(src_module_id):
            graph.add_node(
                src_module_id,
                kind=NodeKind.MODULE.value,
                qualified_name=src_module_qname,
                file=parsed.file.path.as_posix(),
            )

        for edge in parsed.imports:
            self._add_one_import(edge, src_module_id, repo, graph, resolver)

    def _add_one_import(
        self,
        edge: ImportEdge,
        src_module_id: str,
        repo: Repository,
        graph: GraphBackend,
        resolver: ImportResolverProto,
    ) -> None:
        resolved = resolver.resolve(edge, repo)
        if resolved is None:
            # External import: still record an edge to a target module
            # node tagged as external. This way "show me everything that
            # imports requests" works without a separate index.
            target_id = module_node_id(edge.target_module or "<unknown>")
            if not graph.has_node(target_id):
                graph.add_node(
                    target_id,
                    kind=NodeKind.MODULE.value,
                    qualified_name=edge.target_module or "<unknown>",
                    external=True,
                )
            graph.add_edge(
                src_module_id,
                target_id,
                kind=EdgeKind.IMPORTS.value,
                external=True,
            )
            return

        # Internal import: connect to the resolved file's module.
        target_qname = _module_qname_from_path(resolved)
        target_id = module_node_id(target_qname)
        if not graph.has_node(target_id):
            graph.add_node(
                target_id,
                kind=NodeKind.MODULE.value,
                qualified_name=target_qname,
                file=resolved.as_posix(),
            )
        graph.add_edge(
            src_module_id,
            target_id,
            kind=EdgeKind.IMPORTS.value,
            external=False,
        )


# --- helpers ---------------------------------------------------------------


def _module_qname_from_path(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else path.stem
