"""Tests for code intelligence (parser + symbol extractor + import resolver)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoheal.core.models import FileRef, Language, SymbolKind
from repoheal.ingestion import IngestionService
from repoheal.intelligence import PythonImportResolver, TreeSitterParser

# Tree-sitter is an optional native dep at runtime. We skip these tests
# gracefully if the binding isn't installed in the current environment.
pytest.importorskip("tree_sitter_languages")


def _read(repo_root: Path, rel: str) -> bytes:
    return (repo_root / rel).read_bytes()


def _file_ref(repo_root: Path, rel: str, language: Language = Language.PYTHON) -> FileRef:
    p = Path(rel)
    return FileRef(
        path=p,
        language=language,
        size_bytes=(repo_root / rel).stat().st_size,
        is_binary=False,
    )


# --- parser ---------------------------------------------------------------


def test_parser_extracts_python_symbols(tiny_repo: Path) -> None:
    parser = TreeSitterParser()
    file_ref = _file_ref(tiny_repo, "pkg/a.py")
    parsed = parser.parse(file_ref, _read(tiny_repo, "pkg/a.py"))

    qnames = {s.qualified_name for s in parsed.symbols}
    # Module symbol.
    assert "pkg.a" in qnames
    # Top-level function.
    assert "pkg.a.function_in_a" in qnames
    # Class.
    assert "pkg.a.ClassA" in qnames
    # Methods nested under the class.
    assert "pkg.a.ClassA.method_one" in qnames
    assert "pkg.a.ClassA.method_two" in qnames
    # The function nested *inside* method_two should also be present.
    assert "pkg.a.ClassA.method_two.nested" in qnames


def test_parser_classifies_symbol_kinds(tiny_repo: Path) -> None:
    parser = TreeSitterParser()
    file_ref = _file_ref(tiny_repo, "pkg/a.py")
    parsed = parser.parse(file_ref, _read(tiny_repo, "pkg/a.py"))
    by_qname = {s.qualified_name: s for s in parsed.symbols}

    assert by_qname["pkg.a"].kind == SymbolKind.MODULE
    assert by_qname["pkg.a.function_in_a"].kind == SymbolKind.FUNCTION
    assert by_qname["pkg.a.ClassA"].kind == SymbolKind.CLASS
    assert by_qname["pkg.a.ClassA.method_one"].kind == SymbolKind.METHOD


def test_parser_extracts_imports(tiny_repo: Path) -> None:
    parser = TreeSitterParser()
    file_ref = _file_ref(tiny_repo, "pkg/a.py")
    parsed = parser.parse(file_ref, _read(tiny_repo, "pkg/a.py"))
    targets = {e.target_module for e in parsed.imports}
    # ``from pkg import b`` -> target_module == "pkg.b" (full dotted
    # path, so submodules become real edges and cycles are detectable).
    assert "pkg.b" in targets


def test_python_import_resolver_resolves_internal_submodule(tiny_repo: Path) -> None:
    repo = IngestionService().ingest(tiny_repo)
    parser = TreeSitterParser()
    file_ref = _file_ref(tiny_repo, "pkg/a.py")
    parsed = parser.parse(file_ref, _read(tiny_repo, "pkg/a.py"))

    # ``from pkg import b`` -> target_module="pkg.b" -> resolves to pkg/b.py.
    edge = next(e for e in parsed.imports if e.target_module == "pkg.b")
    resolved = PythonImportResolver().resolve(edge, repo)
    assert resolved is not None
    assert resolved.as_posix() == "pkg/b.py"


def test_parser_returns_parsed_file_for_unknown_language(tiny_repo: Path) -> None:
    parser = TreeSitterParser()
    file_ref = FileRef(
        path=Path("README.md"),
        language=Language.UNKNOWN,
        size_bytes=10,
        is_binary=False,
    )
    parsed = parser.parse(file_ref, b"# hello")
    assert parsed.symbols == []
    assert parsed.imports == []


# --- import resolver -------------------------------------------------------


def test_python_import_resolver_resolves_internal_module(tiny_repo: Path) -> None:
    """A ``from pkg import b`` where pkg is the package itself should
    still resolve (to the package's __init__.py) when no submodule
    matches."""
    repo = IngestionService().ingest(tiny_repo)
    parser = TreeSitterParser()
    file_ref = _file_ref(tiny_repo, "pkg/a.py")
    parsed = parser.parse(file_ref, _read(tiny_repo, "pkg/a.py"))

    assert parsed.imports, "expected at least one import edge"
    resolver = PythonImportResolver()
    # All internal imports in pkg/a.py should resolve to a file under pkg/.
    for edge in parsed.imports:
        resolved = resolver.resolve(edge, repo)
        if resolved is not None:
            assert resolved.as_posix().startswith("pkg/")
            assert resolved.suffix == ".py"


def test_python_import_resolver_returns_none_for_external(tiny_repo: Path) -> None:
    repo = IngestionService().ingest(tiny_repo)
    parser = TreeSitterParser()
    file_ref = _file_ref(tiny_repo, "pkg/standalone.py")
    parsed = parser.parse(file_ref, _read(tiny_repo, "pkg/standalone.py"))

    assert parsed.imports
    resolver = PythonImportResolver()
    assert all(resolver.resolve(e, repo) is None for e in parsed.imports if e.target_module == "json")
