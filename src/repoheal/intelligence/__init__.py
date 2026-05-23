"""Code intelligence: turn source bytes into structured symbols and imports.

* :class:`LanguageRegistry` — maps file extension/language to a
  :class:`LanguageDefinition`.
* :class:`TreeSitterParser` — single facade implementing
  :class:`~repoheal.core.protocols.SourceParser`.
* Per-language extractors live next to ``TreeSitterParser``. Phase 1
  ships the Python extractor; others raise
  :class:`~repoheal.exceptions.UnsupportedLanguageError`.

Phase 2:
* :class:`GlobalSymbolIndex`, :class:`FileScope`, :func:`build_scopes`
  and :func:`resolve_dotted` — cross-file identifier resolution used
  by the graph builder to turn unresolved call/inheritance/reference
  records into real graph edges.
"""

from .calls import (
    FileScope,
    GlobalSymbolIndex,
    build_file_scope,
    build_scopes,
    resolve_dotted,
)
from .imports import PythonImportResolver
from .languages import LanguageDefinition, LanguageRegistry, default_registry
from .parser import TreeSitterParser
from .symbols import PythonSymbolExtractor, SymbolExtractorRegistry

__all__ = [
    "FileScope",
    "GlobalSymbolIndex",
    "LanguageDefinition",
    "LanguageRegistry",
    "PythonImportResolver",
    "PythonSymbolExtractor",
    "SymbolExtractorRegistry",
    "TreeSitterParser",
    "build_file_scope",
    "build_scopes",
    "default_registry",
    "resolve_dotted",
]
