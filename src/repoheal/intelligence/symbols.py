"""Per-language symbol extractors.

The :class:`SymbolExtractorRegistry` maps :class:`Language` to an
extractor instance. Phase 1 ships a real Python extractor; the others
are stubs that raise :class:`NotImplementedError` (caught by the parser
facade and recorded as a parse error, never propagated).

Extractor contract (an extension of
:class:`~repoheal.core.protocols.SymbolExtractor`):

* ``extract(parsed, source, *, tree)`` — return a list of
  :class:`~repoheal.core.models.Symbol`.
* ``extract_imports(parsed, source, *, tree)`` — return a list of
  :class:`~repoheal.core.models.ImportEdge`.

Both receive the tree-sitter ``Tree`` because re-parsing is wasteful;
the parser facade already paid that cost.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.models import (
    ImportEdge,
    Language,
    ParsedFile,
    SourceRange,
    Symbol,
    SymbolKind,
)

if TYPE_CHECKING:
    from tree_sitter import Node, Tree


# =============================================================================
# Registry
# =============================================================================


class SymbolExtractorRegistry:
    """Maps language → extractor."""

    def __init__(self) -> None:
        self._by_lang: dict[Language, Any] = {}
        # Default registrations.
        self.register(PythonSymbolExtractor())

    def register(self, extractor: Any) -> None:
        self._by_lang[extractor.language] = extractor

    def get(self, language: Language) -> Any | None:
        return self._by_lang.get(language)


# =============================================================================
# Python extractor (real)
# =============================================================================


class PythonSymbolExtractor:
    """Extract symbols and imports from a Python tree-sitter parse tree.

    Recognises:

    * top-level and nested ``function_definition`` (incl. ``async def``);
    * ``class_definition``;
    * methods (functions whose enclosing scope is a class);
    * module-level ``import`` and ``from … import`` statements.

    Qualified names are dotted, rooted at the module path:
    ``pkg.sub.module.ClassName.method``.
    """

    @property
    def language(self) -> Language:
        return Language.PYTHON

    # ------------------------------------------------------------------

    def extract(
        self,
        parsed: ParsedFile,
        source: bytes,
        *,
        tree: Tree | None = None,
    ) -> Sequence[Symbol]:
        if tree is None:
            return []

        module_name = _module_qualified_name(parsed.file.path)
        symbols: list[Symbol] = []

        # Emit one Symbol for the module itself.
        root = tree.root_node
        symbols.append(
            Symbol(
                name=parsed.file.path.stem,
                qualified_name=module_name,
                kind=SymbolKind.MODULE,
                file=parsed.file.path,
                range=_range_from_node(root),
                parent=None,
            )
        )

        self._walk(
            node=root,
            source=source,
            file_path=parsed.file.path,
            parent_qualified=module_name,
            in_class=False,
            out=symbols,
        )
        return symbols

    # ------------------------------------------------------------------

    def extract_imports(
        self,
        parsed: ParsedFile,
        source: bytes,
        *,
        tree: Tree | None = None,
    ) -> Sequence[ImportEdge]:
        if tree is None:
            return []

        edges: list[ImportEdge] = []
        cursor = tree.walk()
        nodes = _walk_named(cursor)
        for node in nodes:
            if node.type == "import_statement":
                edges.extend(self._import_edges_from_import(node, source, parsed.file.path))
            elif node.type == "import_from_statement":
                edges.extend(
                    self._import_edges_from_import_from(node, source, parsed.file.path)
                )
        return edges

    # ------------------------------------------------------------------
    # Internal walk
    # ------------------------------------------------------------------

    def _walk(
        self,
        *,
        node: Node,
        source: bytes,
        file_path: Path,
        parent_qualified: str,
        in_class: bool,
        out: list[Symbol],
    ) -> None:
        for child in node.children:
            if child.type == "function_definition":
                name = _named_child_text(child, "name", source) or "<anonymous>"
                kind = SymbolKind.METHOD if in_class else SymbolKind.FUNCTION
                qname = f"{parent_qualified}.{name}"
                out.append(
                    Symbol(
                        name=name,
                        qualified_name=qname,
                        kind=kind,
                        file=file_path,
                        range=_range_from_node(child),
                        parent=parent_qualified,
                    )
                )
                # Descend into the function body for nested defs.
                body = child.child_by_field_name("body")
                if body is not None:
                    self._walk(
                        node=body,
                        source=source,
                        file_path=file_path,
                        parent_qualified=qname,
                        in_class=False,
                        out=out,
                    )

            elif child.type == "class_definition":
                name = _named_child_text(child, "name", source) or "<anonymous>"
                qname = f"{parent_qualified}.{name}"
                out.append(
                    Symbol(
                        name=name,
                        qualified_name=qname,
                        kind=SymbolKind.CLASS,
                        file=file_path,
                        range=_range_from_node(child),
                        parent=parent_qualified,
                    )
                )
                body = child.child_by_field_name("body")
                if body is not None:
                    self._walk(
                        node=body,
                        source=source,
                        file_path=file_path,
                        parent_qualified=qname,
                        in_class=True,
                        out=out,
                    )

            elif child.type == "decorated_definition":
                # Recurse into the wrapped definition; decorators handled by
                # downstream metadata if we ever care. The tree-sitter
                # grammar nests the real def inside.
                self._walk(
                    node=child,
                    source=source,
                    file_path=file_path,
                    parent_qualified=parent_qualified,
                    in_class=in_class,
                    out=out,
                )

            else:
                # Module-level statements other than def/class are fine to
                # peek into for nested function defs (e.g. inside `if
                # __name__ == "__main__":`). For class bodies we stop
                # because their own walker handles methods.
                if not in_class and child.named_child_count > 0:
                    self._walk(
                        node=child,
                        source=source,
                        file_path=file_path,
                        parent_qualified=parent_qualified,
                        in_class=False,
                        out=out,
                    )

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _import_edges_from_import(
        self,
        node: Node,
        source: bytes,
        file_path: Path,
    ) -> list[ImportEdge]:
        """Handle ``import x[, y as z]`` statements."""
        edges: list[ImportEdge] = []
        for child in node.named_children:
            if child.type == "dotted_name":
                module = _node_text(child, source)
                edges.append(
                    ImportEdge(
                        source_file=file_path,
                        target_module=module,
                        is_relative=False,
                    )
                )
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node is None:
                    continue
                module = _node_text(name_node, source)
                alias = _node_text(alias_node, source) if alias_node else None
                edges.append(
                    ImportEdge(
                        source_file=file_path,
                        target_module=module,
                        alias=alias,
                        is_relative=False,
                    )
                )
        return edges

    def _import_edges_from_import_from(
        self,
        node: Node,
        source: bytes,
        file_path: Path,
    ) -> list[ImportEdge]:
        """Handle ``from x import a, b`` statements.

        Python semantics: ``from pkg import b`` will load ``pkg.b`` as a
        submodule if ``pkg/b.py`` exists. We therefore expand each
        imported name into its own edge with the *full dotted path*
        (``pkg.b``) as the target module. The resolver tries that path
        first and falls back to the parent module if the name turns out
        to be an attribute rather than a submodule.
        """
        module_node = node.child_by_field_name("module_name")
        is_relative = False
        module_text = ""
        if module_node is not None:
            module_text = _node_text(module_node, source)
            is_relative = module_text.startswith(".")

        # Collect the imported names (each one becomes its own edge).
        imports: list[tuple[str, str | None]] = []  # (name, alias)
        seen_module = module_node is None
        for child in node.named_children:
            if not seen_module:
                if child is module_node:
                    seen_module = True
                continue
            if child.type in ("dotted_name", "identifier"):
                imports.append((_node_text(child, source), None))
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node is not None:
                    imports.append(
                        (
                            _node_text(name_node, source),
                            _node_text(alias_node, source) if alias_node else None,
                        )
                    )
            elif child.type == "wildcard_import":
                # ``from x import *`` — record an edge to the module itself.
                imports.append(("*", None))

        edges: list[ImportEdge] = []
        for name, alias in imports:
            if name == "*":
                target = module_text
            elif is_relative:
                # ``from . import b``  → target ".b"
                # ``from .pkg import b`` → target ".pkg.b"
                separator = "" if module_text.endswith(".") or not module_text else "."
                target = f"{module_text}{separator}{name}" if module_text else f".{name}"
            else:
                target = f"{module_text}.{name}" if module_text else name

            edges.append(
                ImportEdge(
                    source_file=file_path,
                    target_module=target,
                    is_relative=is_relative,
                    alias=alias,
                )
            )

        # Deduplicate edges with identical (target, relative) pairs;
        # ``from x import a as one, a as two`` should yield one edge.
        deduped: dict[tuple[str, bool], ImportEdge] = {}
        for e in edges:
            key = (e.target_module, e.is_relative)
            deduped.setdefault(key, e)
        return list(deduped.values())


# =============================================================================
# Helpers
# =============================================================================


def _module_qualified_name(file_path: Path) -> str:
    """Convert ``pkg/sub/mod.py`` → ``pkg.sub.mod``."""
    parts = list(file_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else file_path.stem


def _range_from_node(node: Node) -> SourceRange:
    return SourceRange(
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point[0],
        start_col=node.start_point[1],
        end_line=node.end_point[0],
        end_col=node.end_point[1],
    )


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _named_child_text(node: Node, field: str, source: bytes) -> str | None:
    child = node.child_by_field_name(field)
    if child is None:
        return None
    return _node_text(child, source)


def _walk_named(cursor: Any) -> list[Node]:
    """Iteratively walk every named node of a tree-sitter tree."""
    out: list[Node] = []
    visited: set[int] = set()

    def push(node: Node) -> None:
        if id(node) in visited:
            return
        visited.add(id(node))
        out.append(node)
        for c in node.named_children:
            push(c)

    push(cursor.node)
    return out
