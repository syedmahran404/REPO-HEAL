"""Language registry.

A :class:`LanguageDefinition` tells the parser two things:

* what tree-sitter grammar identifier to use (a string accepted by
  ``tree_sitter_languages.get_parser``);
* what file extensions belong to the language.

The registry is constructed once at import time and reused. Adding a
language is a one-line change here plus a real ``SymbolExtractor`` for
that language in :mod:`repoheal.intelligence.symbols`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import Language


@dataclass(frozen=True)
class LanguageDefinition:
    """Configuration for a single language."""

    language: Language
    tree_sitter_name: str  # passed to tree_sitter_languages.get_parser
    extensions: tuple[str, ...] = field(default_factory=tuple)


class LanguageRegistry:
    """Registry of supported languages."""

    def __init__(self, definitions: list[LanguageDefinition] | None = None) -> None:
        self._by_language: dict[Language, LanguageDefinition] = {}
        self._by_extension: dict[str, LanguageDefinition] = {}
        if definitions:
            for d in definitions:
                self.register(d)

    def register(self, definition: LanguageDefinition) -> None:
        self._by_language[definition.language] = definition
        for ext in definition.extensions:
            self._by_extension[ext.lower()] = definition

    def get(self, language: Language) -> LanguageDefinition | None:
        return self._by_language.get(language)

    def for_extension(self, ext: str) -> LanguageDefinition | None:
        return self._by_extension.get(ext.lower())

    def supported_languages(self) -> list[Language]:
        return list(self._by_language)


# --- default registry ------------------------------------------------------

_DEFINITIONS: list[LanguageDefinition] = [
    LanguageDefinition(Language.PYTHON, "python", (".py", ".pyi")),
    LanguageDefinition(Language.JAVASCRIPT, "javascript", (".js", ".jsx", ".mjs", ".cjs")),
    LanguageDefinition(Language.TYPESCRIPT, "typescript", (".ts",)),
    LanguageDefinition(Language.JAVA, "java", (".java",)),
    LanguageDefinition(Language.GO, "go", (".go",)),
    LanguageDefinition(Language.RUST, "rust", (".rs",)),
    LanguageDefinition(Language.C, "c", (".c", ".h")),
    LanguageDefinition(Language.CPP, "cpp", (".cc", ".cpp", ".cxx", ".hpp")),
    LanguageDefinition(Language.CSHARP, "c_sharp", (".cs",)),
    LanguageDefinition(Language.PHP, "php", (".php",)),
    LanguageDefinition(Language.KOTLIN, "kotlin", (".kt", ".kts")),
    LanguageDefinition(Language.RUBY, "ruby", (".rb",)),
    # Swift has no first-party tree-sitter-languages binding bundled in some
    # versions; we still register the language but parsing will raise
    # UnsupportedLanguageError gracefully.
    LanguageDefinition(Language.SWIFT, "swift", (".swift",)),
]


def default_registry() -> LanguageRegistry:
    """Construct the canonical language registry."""
    return LanguageRegistry(list(_DEFINITIONS))
