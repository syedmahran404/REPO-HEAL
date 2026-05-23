"""Import resolution.

Given an :class:`~repoheal.core.models.ImportEdge` and a
:class:`~repoheal.core.models.Repository`, decide which file (if any) in
the repo the import resolves to.

Phase 1 implements Python; the resolver is a Protocol so other languages
can be added without changing the graph builder.
"""

from __future__ import annotations

from pathlib import Path

from ..core.models import ImportEdge, Repository


class PythonImportResolver:
    """Resolve a Python import to a file path inside the repo, if possible.

    Strategy:

    1. If the edge is relative (``from . import x``), resolve relative to
       the importing file's package directory.
    2. Otherwise, treat ``a.b.c`` as a candidate file path
       ``a/b/c.py`` *or* ``a/b/c/__init__.py`` and search for it under
       any of the repo's roots (a "root" is a directory containing the
       top package, identified by the presence of ``__init__.py``).
    3. Return the first match; ``None`` if not found (third-party or
       standard library).

    The resolver is intentionally conservative: it never invents files.
    """

    def __init__(self) -> None:
        # Cache: repository.id → list of candidate package roots.
        self._roots_cache: dict[str, list[Path]] = {}

    # ------------------------------------------------------------------

    def resolve(self, edge: ImportEdge, repo: Repository) -> Path | None:
        if edge.is_relative:
            return self._resolve_relative(edge, repo)
        return self._resolve_absolute(edge, repo)

    # ------------------------------------------------------------------

    def _resolve_relative(self, edge: ImportEdge, repo: Repository) -> Path | None:
        # target_module is like ``.x``, ``..pkg.sub``, or ``.``.
        module = edge.target_module
        dots = 0
        while dots < len(module) and module[dots] == ".":
            dots += 1
        rest = module[dots:]

        importing_dir = (repo.root / edge.source_file).parent
        # one dot means current package; each extra dot ascends.
        target_dir = importing_dir
        for _ in range(dots - 1):
            target_dir = target_dir.parent

        candidate_parts = rest.split(".") if rest else []

        # Try the full path first; if that fails, try without the last
        # segment (the imported name might be an attribute, not a module).
        hit = _try_module_paths(repo.root, target_dir, candidate_parts)
        if hit is not None:
            return hit
        if len(candidate_parts) > 1:
            return _try_module_paths(repo.root, target_dir, candidate_parts[:-1])
        return None

    def _resolve_absolute(self, edge: ImportEdge, repo: Repository) -> Path | None:
        roots = self._candidate_roots(repo)
        parts = edge.target_module.split(".")
        for root in roots:
            hit = _try_module_paths(repo.root, root, parts)
            if hit is not None:
                return hit
        # Fallback: the last segment may be an attribute (function /
        # class) of the parent module rather than a submodule. Try
        # again with the parent path.
        if len(parts) > 1:
            for root in roots:
                hit = _try_module_paths(repo.root, root, parts[:-1])
                if hit is not None:
                    return hit
        return None

    # ------------------------------------------------------------------

    def _candidate_roots(self, repo: Repository) -> list[Path]:
        cache_key = str(repo.id)
        cached = self._roots_cache.get(cache_key)
        if cached is not None:
            return cached

        roots: list[Path] = [repo.root]

        # Common src layouts.
        for candidate in ("src", "lib", "app"):
            sub = repo.root / candidate
            if sub.is_dir():
                roots.append(sub)

        self._roots_cache[cache_key] = roots
        return roots


# --- helpers --------------------------------------------------------------


def _try_module_paths(repo_root: Path, base: Path, parts: list[str]) -> Path | None:
    """Look for ``base/parts[0]/.../parts[-1].py`` or its package init.

    Returns the path **relative to the repo root**.
    """
    if not parts:
        # ``from . import …`` with no rest → current package's __init__.py
        candidate = base / "__init__.py"
        if candidate.is_file():
            return _safe_relative(candidate, repo_root)
        return None

    file_candidate = base.joinpath(*parts).with_suffix(".py")
    pkg_candidate = base.joinpath(*parts) / "__init__.py"

    if file_candidate.is_file():
        return _safe_relative(file_candidate, repo_root)
    if pkg_candidate.is_file():
        return _safe_relative(pkg_candidate, repo_root)

    # Try with progressively shorter suffixes (e.g. ``a.b.c.func`` where
    # ``func`` is a symbol inside ``a/b/c.py``).
    for end in range(len(parts) - 1, 0, -1):
        head = parts[:end]
        file_c = base.joinpath(*head).with_suffix(".py")
        pkg_c = base.joinpath(*head) / "__init__.py"
        if file_c.is_file():
            return _safe_relative(file_c, repo_root)
        if pkg_c.is_file():
            return _safe_relative(pkg_c, repo_root)

    return None


def _safe_relative(child: Path, parent: Path) -> Path | None:
    try:
        return child.resolve().relative_to(parent.resolve())
    except ValueError:
        return None
