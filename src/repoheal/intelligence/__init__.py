"""Code intelligence: turn source bytes into structured symbols and imports.

* :class:`LanguageRegistry` — maps file extension/language to a
  :class:`LanguageDefinition`.
* :class:`TreeSitterParser` — single facade implementing
  :class:`~repoheal.core.protocols.SourceParser`.
* Per-language extractors live next to ``TreeSitterParser``. Phase 1
  ships the Python extractor; others raise
  :class:`~repoheal.exceptions.UnsupportedLanguageError`.
"""

from .imports import PythonImportResolver
from .languages import LanguageDefinition, LanguageRegistry, default_registry
from .parser import TreeSitterParser
from .symbols import PythonSymbolExtractor, SymbolExtractorRegistry

__all__ = [
    "LanguageDefinition",
    "LanguageRegistry",
    "PythonImportResolver",
    "PythonSymbolExtractor",
    "SymbolExtractorRegistry",
    "TreeSitterParser",
    "default_registry",
]
