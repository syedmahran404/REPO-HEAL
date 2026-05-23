"""Cross-file resolution for call / inheritance / reference edges.

The parser layer is purely lexical: it emits ``UnresolvedCall``,
``UnresolvedInheritance``, ``UnresolvedReference`` records carrying the
textual callee/parent/target as written in the source. *This* module
takes those records plus a global symbol index and resolves them into
graph edges.

Two resolution strategies are implemented (ADR-0006):

1. **Heuristic** (default, no extra deps). Builds per-file scopes from
   the symbol table + the import edges already produced by the parser,
   then resolves textual callees against a scope chain. Drops anything
   ambiguous.

2. **Type-aware** (planned; not in this PR). A `pyright`-backed resolver
   plugged in behind the same Protocol.

Returning ``None`` from the resolver is a normal outcome — it means
"this call points at something we don't index" (third-party library,
stdlib, dynamic dispatch). Detection rules built on top must be
robust to false negatives.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ..core.models import (
    ImportEdge,
    ParsedFile,
    Repository,
    Symbol,
    SymbolKind,
)
from ..core.protocols import ImportResolver as ImportResolverProto
from .imports import PythonImportResolver


# =============================================================================
# Global symbol index
# =============================================================================


class GlobalSymbolIndex:
    """Map qualified name → Symbol across every parsed file.

    Cheap to build (single pass, dict insert), cheap to query
    (dict lookup). The whole index for a 50k-file repo with ~500k
    symbols fits in tens of MiB.
    """

    def __init__(self, parsed_files: Iterable[ParsedFile]) -> None:
        self._by_qname: dict[str, Symbol] = {}
        # Used by `resolve_attribute` for ``Class.method`` style lookups.
        self._members_of: dict[str, list[str]] = {}

        for parsed in parsed_files:
            for sym in parsed.symbols:
                self._by_qname[sym.qualified_name] = sym
                if sym.parent is not None:
                    self._members_of.setdefault(sym.parent, []).append(sym.qualified_name)

    def has(self, qname: str) -> bool:
        return qname in self._by_qname

    def get(self, qname: str) -> Symbol | None:
        return self._by_qname.get(qname)

    def members(self, qname: str) -> list[str]:
        """Qualified names of children of the given symbol (e.g. methods of a class)."""
        return list(self._members_of.get(qname, ()))

    def __len__(self) -> int:
        return len(self._by_qname)

    def all_callable_qnames(self) -> Iterable[str]:
        """Iterate over qnames of every function and method."""
        for qn, sym in self._by_qname.items():
            if sym.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                yield qn


# =============================================================================
# Per-file scope
# =============================================================================


@dataclass
class FileScope:
    """The names visible at module level in one Python file.

    * ``top_level`` — simple_name → qualified_name for symbols defined
      in this file at module level.
    * ``imports`` — local_name → target_qname for everything brought in
      by ``import`` / ``from … import`` statements. The target is the
      qualified name we *would* find in the global index if it's
      internal; for external imports it's the fully-qualified module
      name as written.
    """

    module_qname: str
    top_level: dict[str, str] = field(default_factory=dict)
    imports: dict[str, str] = field(default_factory=dict)

    def lookup(self, simple_name: str) -> str | None:
        """Resolve a simple identifier to a qualified name, or None."""
        if simple_name in self.top_level:
            return self.top_level[simple_name]
        if simple_name in self.imports:
            return self.imports[simple_name]
        return None


def build_file_scope(
    parsed: ParsedFile,
    repo: Repository,
    *,
    resolver: ImportResolverProto | None = None,
) -> FileScope:
    """Build a :class:`FileScope` for a parsed Python file.

    Top-level symbols are anything whose ``parent`` is the module's
    qualified name, or whose ``kind`` is ``MODULE``. Imports are read
    from ``parsed.imports``; for each, we ask the resolver whether
    the target is internal — if so, the bound name maps to the
    target's module qualified name (or to ``module.symbol`` when the
    import was ``from x import y``).
    """
    resolver = resolver or PythonImportResolver()
    module_qname = _module_qname_from_parsed(parsed)
    scope = FileScope(module_qname=module_qname)

    for sym in parsed.symbols:
        if sym.kind == SymbolKind.MODULE:
            continue
        # Top-level: parent is the module itself.
        if sym.parent == module_qname:
            scope.top_level[sym.name] = sym.qualified_name

    for edge in parsed.imports:
        bound_names = _bound_names_for_import(edge)
        target_qname = _target_qname_for_import(edge, repo, resolver)
        for bound in bound_names:
            scope.imports[bound] = target_qname

    return scope


def build_scopes(
    parsed_files: Iterable[ParsedFile],
    repo: Repository,
    *,
    resolver: ImportResolverProto | None = None,
) -> dict[str, FileScope]:
    """Build a scope per parsed Python file, keyed by module qualified name."""
    resolver = resolver or PythonImportResolver()
    out: dict[str, FileScope] = {}
    for parsed in parsed_files:
        scope = build_file_scope(parsed, repo, resolver=resolver)
        out[scope.module_qname] = scope
    return out


# =============================================================================
# Resolution
# =============================================================================


def resolve_dotted(
    text: str,
    scope: FileScope,
    index: GlobalSymbolIndex,
) -> str | None:
    """Resolve a textual identifier (possibly dotted) to a known qualified
    name in the global symbol index, or return None.

    Strategy:

    * For ``foo``: look up ``foo`` in the file scope; if it's a
      qualified name we know about, return it.
    * For ``a.b.c``: resolve the head ``a`` against the scope; append
      ``.b.c``; check the index. If not found, try shorter suffixes
      (the trailing components might be attributes on a class we don't
      know the class of).

    Always conservative: returns None on ambiguity or miss.
    """
    text = text.strip()
    if not text:
        return None

    parts = text.split(".")

    # Drop pure expressions that can't be resolved.
    if any(not p.isidentifier() for p in parts):
        return None

    head = parts[0]
    qualified_head = scope.lookup(head)

    if qualified_head is None:
        return None

    if len(parts) == 1:
        return qualified_head if index.has(qualified_head) else None

    # Try the full join; if absent, peel off trailing parts (which might
    # be method/attribute accesses we don't index in the symbol table).
    full = f"{qualified_head}.{'.'.join(parts[1:])}"
    if index.has(full):
        return full

    for end in range(len(parts) - 1, 0, -1):
        candidate = f"{qualified_head}.{'.'.join(parts[1:end])}" if end > 1 else qualified_head
        if index.has(candidate):
            return candidate

    return None


# =============================================================================
# Helpers
# =============================================================================


def _module_qname_from_parsed(parsed: ParsedFile) -> str:
    """Mirror of the path → qname conversion used elsewhere in the package."""
    parts = list(parsed.file.path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else parsed.file.path.stem


def _bound_names_for_import(edge: ImportEdge) -> list[str]:
    """The local names ``edge`` introduces in the importing module's namespace.

    * ``import x``         → [``x``]
    * ``import x.y``       → [``x``]            (only the head is bound)
    * ``import x as z``    → [``z``]
    * ``from x import y``  → [``y``]            (target_module is "x.y" by Phase 1 convention)
    * ``from x import y as z`` → [``z``]
    * ``from . import y``  → [``y``]
    """
    if edge.alias:
        return [edge.alias]

    target = edge.target_module
    # "from x import y" -> target == "x.y". The bound name is "y", the last segment.
    # "import x.y"      -> target == "x.y". The bound name is "x", the head.
    # We can disambiguate by whether the parser saw a from-import: that's
    # what `is_relative` tracks for relative imports, but absolute from-imports
    # aren't flagged. The cleanest heuristic: if there's NO alias and the
    # target_module has dots, both the head and the tail are reasonable
    # candidates. We bind the tail because it dominates Python style; a
    # from-import is far more common than a multi-segment `import a.b.c`.
    if "." in target:
        return [target.split(".")[-1]]
    return [target]


def _target_qname_for_import(
    edge: ImportEdge,
    repo: Repository,
    resolver: ImportResolverProto,
) -> str:
    """Best-effort qualified name for what an import points at.

    For internal modules we return the module's qname (derived from its
    file path); for external imports we return the textual target.
    """
    resolved = resolver.resolve(edge, repo)
    if resolved is None:
        # External: trust target_module. Strip leading dots for relative
        # forms — they don't make sense as a qualified name.
        return edge.target_module.lstrip(".")
    parts = list(resolved.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else resolved.stem


__all__ = [
    "FileScope",
    "GlobalSymbolIndex",
    "build_file_scope",
    "build_scopes",
    "resolve_dotted",
]
