"""Graph builder: turn parsed files into graph nodes and edges.

Pure transformation. Given a repository, a stream of
:class:`~repoheal.core.models.ParsedFile`, and a
:class:`~repoheal.core.protocols.GraphBackend`, it populates the
backend.

Three passes:

1. **Files & symbols** — emit ``File``, ``Module``, ``Class``,
   ``Function``, ``Method`` nodes and ``CONTAINS`` edges.
2. **Imports** — resolve and emit ``IMPORTS`` edges between modules.
3. **Calls / inheritance / references** *(Phase 2)* — build a global
   symbol index plus per-file scopes, then resolve the textual
   callees/parents/decorators emitted by the Python extractor and
   add ``CALLS``, ``INHERITS``, ``REFERENCES`` edges.

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
    UnresolvedCall,
    UnresolvedInheritance,
    UnresolvedReference,
)
from ..core.protocols import GraphBackend, ImportResolver as ImportResolverProto
from ..intelligence.calls import (
    FileScope,
    GlobalSymbolIndex,
    build_scopes,
    resolve_dotted,
)
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
    """Populate a graph backend from parsed files."""

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
        """Build (or extend) the graph from the given parsed files.

        Idempotent on repeated calls with the same input."""
        parsed_list = list(parsed_files)

        for parsed in parsed_list:
            self._add_file_and_symbols(parsed, graph)

        for parsed in parsed_list:
            self._add_imports(parsed, repo, graph)

        # Phase 2: build global index + scopes for Python files,
        # then resolve and emit CALLS/INHERITS/REFERENCES edges.
        python_files = [pf for pf in parsed_list if pf.language == Language.PYTHON]
        if python_files:
            index = GlobalSymbolIndex(python_files)
            resolver = self._resolvers.get(Language.PYTHON)
            scopes = build_scopes(python_files, repo, resolver=resolver)

            for parsed in python_files:
                scope = scopes.get(_module_qname_from_path(parsed.file.path))
                if scope is None:
                    continue
                for call in parsed.calls:
                    self._add_call_edge(call, scope, index, graph)
                for inh in parsed.inherits:
                    self._add_inheritance_edge(inh, scope, index, graph)
                for ref in parsed.references:
                    self._add_reference_edge(ref, scope, index, graph)

        _log.info(
            "graph.build_done",
            nodes=graph.node_count(),
            edges=graph.edge_count(),
        )

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

            if symbol.parent is None or symbol.kind == SymbolKind.MODULE:
                graph.add_edge(file_id, sym_id, kind=EdgeKind.CONTAINS.value)
            else:
                parent_sym = by_qname.get(symbol.parent)
                if parent_sym is not None:
                    parent_kind = _SYMBOL_KIND_TO_NODE_KIND.get(parent_sym.kind)
                    if parent_kind is not None:
                        parent_id = symbol_node_id(
                            parent_sym.qualified_name, kind=parent_kind
                        )
                        graph.add_edge(parent_id, sym_id, kind=EdgeKind.CONTAINS.value)
                else:
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
            return

        src_module_qname = _module_qname_from_path(parsed.file.path)
        src_module_id = module_node_id(src_module_qname)
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

    # ------------------------------------------------------------------
    # Phase 2: resolve calls / inheritance / references
    # ------------------------------------------------------------------

    def _add_call_edge(
        self,
        call: UnresolvedCall,
        scope: FileScope,
        index: GlobalSymbolIndex,
        graph: GraphBackend,
    ) -> None:
        target_qname = resolve_dotted(call.callee_text, scope, index)
        if target_qname is None:
            return
        target_id = self._symbol_node_id_or_none(target_qname, index)
        if target_id is None:
            return
        caller_id = self._caller_node_id(call.caller_qname, index, graph)
        if caller_id is None:
            return
        graph.add_edge(
            caller_id,
            target_id,
            kind=EdgeKind.CALLS.value,
            callee_text=call.callee_text,
        )

    def _add_inheritance_edge(
        self,
        inh: UnresolvedInheritance,
        scope: FileScope,
        index: GlobalSymbolIndex,
        graph: GraphBackend,
    ) -> None:
        parent_qname = resolve_dotted(inh.parent_text, scope, index)
        if parent_qname is None:
            return
        parent_id = self._symbol_node_id_or_none(parent_qname, index)
        if parent_id is None:
            return
        child_id = self._symbol_node_id_or_none(inh.child_qname, index)
        if child_id is None:
            return
        graph.add_edge(
            child_id,
            parent_id,
            kind=EdgeKind.INHERITS.value,
            parent_text=inh.parent_text,
        )

    def _add_reference_edge(
        self,
        ref: UnresolvedReference,
        scope: FileScope,
        index: GlobalSymbolIndex,
        graph: GraphBackend,
    ) -> None:
        target_qname = resolve_dotted(ref.target_text, scope, index)
        if target_qname is None:
            return
        target_id = self._symbol_node_id_or_none(target_qname, index)
        if target_id is None:
            return
        referrer_id = self._symbol_node_id_or_none(ref.referrer_qname, index)
        if referrer_id is None:
            return
        graph.add_edge(
            referrer_id,
            target_id,
            kind=EdgeKind.REFERENCES.value,
            target_text=ref.target_text,
        )

    # ------------------------------------------------------------------

    def _symbol_node_id_or_none(
        self,
        qname: str,
        index: GlobalSymbolIndex,
    ) -> str | None:
        sym = index.get(qname)
        if sym is None:
            return None
        node_kind = _SYMBOL_KIND_TO_NODE_KIND.get(sym.kind)
        if node_kind is None:
            return None
        return symbol_node_id(qname, kind=node_kind)

    def _caller_node_id(
        self,
        caller_qname: str,
        index: GlobalSymbolIndex,
        graph: GraphBackend,
    ) -> str | None:
        sym = index.get(caller_qname)
        if sym is not None:
            node_kind = _SYMBOL_KIND_TO_NODE_KIND.get(sym.kind)
            if node_kind is None:
                return None
            return symbol_node_id(caller_qname, kind=node_kind)
        # Caller is the module itself (call at module level).
        module_id = module_node_id(caller_qname)
        if graph.has_node(module_id):
            return module_id
        return None


# --- helpers ---------------------------------------------------------------


def _module_qname_from_path(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else path.stem
