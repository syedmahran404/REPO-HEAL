"""Tree-sitter parser facade.

Wraps ``tree_sitter_languages.get_parser`` with:

* a per-thread parser cache (tree-sitter parser objects are not
  thread-safe; we keep one per ``threading.local``);
* graceful failure when a grammar isn't available (we record a parse
  error on the :class:`~repoheal.core.models.ParsedFile` and continue);
* the connection to the per-language symbol extractor registry.

Public Protocol implemented: :class:`~repoheal.core.protocols.SourceParser`.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.models import FileRef, Language, ParsedFile
from ..exceptions import UnsupportedLanguageError
from ..logging import get_logger
from .languages import LanguageDefinition, LanguageRegistry, default_registry
from .symbols import SymbolExtractorRegistry

if TYPE_CHECKING:
    from tree_sitter import Parser, Tree  # noqa: F401

_log = get_logger(__name__)


class TreeSitterParser:
    """Single parsing entry point for every supported language."""

    def __init__(
        self,
        registry: LanguageRegistry | None = None,
        extractor_registry: SymbolExtractorRegistry | None = None,
    ) -> None:
        self._registry = registry or default_registry()
        self._extractors = extractor_registry or SymbolExtractorRegistry()
        # Per-thread parser cache, since tree-sitter parsers are not
        # thread-safe. Each thread lazily builds its own.
        self._tls = threading.local()

    # ------------------------------------------------------------------

    def parse(self, file: FileRef, source: bytes) -> ParsedFile:
        """Parse a single file. Always returns a ``ParsedFile``;
        parse errors are recorded, not raised."""
        if file.is_binary or file.language == Language.UNKNOWN:
            return ParsedFile(file=file, language=file.language)

        definition = self._registry.get(file.language)
        if definition is None:
            return ParsedFile(
                file=file,
                language=file.language,
                parse_errors=[f"language not registered: {file.language.value}"],
            )

        try:
            parser = self._parser_for(definition)
        except UnsupportedLanguageError as exc:
            return ParsedFile(
                file=file,
                language=file.language,
                parse_errors=[str(exc)],
            )

        try:
            tree = parser.parse(source)
        except Exception as exc:  # tree-sitter raises bare Exceptions
            _log.warning(
                "parser.parse_failed",
                path=str(file.path),
                language=file.language.value,
                error=str(exc),
            )
            return ParsedFile(
                file=file,
                language=file.language,
                parse_errors=[f"tree-sitter parse error: {exc}"],
            )

        parsed = ParsedFile(file=file, language=file.language)

        # Run the per-language symbol extractor if registered.
        extractor = self._extractors.get(file.language)
        if extractor is not None:
            try:
                symbols = extractor.extract(parsed, source, tree=tree)
                parsed = parsed.model_copy(update={"symbols": list(symbols)})
            except NotImplementedError:
                # Extractor exists but is a stub (other languages).
                parsed = parsed.model_copy(
                    update={
                        "parse_errors": [*parsed.parse_errors, "extractor not implemented"]
                    }
                )
            except Exception as exc:  # defensive: extractor bug shouldn't kill ingest
                _log.warning(
                    "parser.extractor_failed",
                    path=str(file.path),
                    error=str(exc),
                )
                parsed = parsed.model_copy(
                    update={"parse_errors": [*parsed.parse_errors, f"extractor error: {exc}"]}
                )

            # Imports are extracted alongside symbols for languages that
            # support it. Only Python implements it in Phase 1.
            try:
                imports = extractor.extract_imports(parsed, source, tree=tree)
                parsed = parsed.model_copy(update={"imports": list(imports)})
            except NotImplementedError:
                pass
            except Exception as exc:
                _log.warning(
                    "parser.imports_failed",
                    path=str(file.path),
                    error=str(exc),
                )

            # Phase 2: calls, inheritance, references — all optional per
            # extractor (Python implements; others raise NotImplementedError).
            for method, field in (
                ("extract_calls", "calls"),
                ("extract_inheritance", "inherits"),
                ("extract_references", "references"),
            ):
                fn = getattr(extractor, method, None)
                if fn is None:
                    continue
                try:
                    items = fn(parsed, source, tree=tree)
                    parsed = parsed.model_copy(update={field: list(items)})
                except NotImplementedError:
                    continue
                except Exception as exc:
                    _log.warning(
                        "parser.extractor_phase2_failed",
                        method=method,
                        path=str(file.path),
                        error=str(exc),
                    )

        return parsed

    # ------------------------------------------------------------------

    def _parser_for(self, definition: LanguageDefinition) -> Any:
        """Return a tree-sitter parser, lazily built per thread."""
        cache: dict[str, Any] = getattr(self._tls, "cache", {})
        if definition.tree_sitter_name in cache:
            return cache[definition.tree_sitter_name]

        try:
            from tree_sitter_languages import get_parser  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise UnsupportedLanguageError(
                "tree_sitter_languages is not installed; "
                "install with `pip install tree-sitter-languages`"
            ) from exc

        try:
            parser = get_parser(definition.tree_sitter_name)
        except Exception as exc:  # tree_sitter raises bare Exception
            raise UnsupportedLanguageError(
                f"no tree-sitter grammar available for "
                f"{definition.language.value!r} ({definition.tree_sitter_name!r}): {exc}"
            ) from exc

        cache[definition.tree_sitter_name] = parser
        self._tls.cache = cache
        return parser

    # ------------------------------------------------------------------

    def parse_path(self, root: Path, file: FileRef) -> ParsedFile:
        """Convenience wrapper that reads file content from disk."""
        try:
            source = (root / file.path).read_bytes()
        except OSError as exc:
            return ParsedFile(
                file=file,
                language=file.language,
                parse_errors=[f"could not read file: {exc}"],
            )
        return self.parse(file, source)
