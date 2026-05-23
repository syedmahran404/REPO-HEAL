"""Symbol-aware chunking.

A chunk is the atomic unit retrieval can return. We refuse to split a
function across chunks — the graph already knows where the boundaries
are, so we use them.

Strategy:

* For each Python file with a parsed symbol table, emit one chunk per
  ``FUNCTION`` / ``METHOD`` symbol covering its source range.
* Emit a "module preamble" chunk covering everything before the first
  symbol's start line (imports, module docstring, top-level
  assignments).
* If a file has zero functions/methods (e.g. a config file or a tiny
  ``__init__.py``), emit a single chunk for the whole file (capped to
  a configurable line limit so a giant generated file doesn't bomb the
  index).
* Files in unsupported languages get the whole-file fallback as well.

Chunks are deterministic: the same file with the same content always
produces the same chunks with the same ids. This is the contract the
incremental-indexing layer relies on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..core.models import (
    Chunk,
    FileRef,
    Language,
    ParsedFile,
    Symbol,
    SymbolKind,
)


@dataclass(frozen=True)
class ChunkingConfig:
    """Tunables for the chunker."""

    max_whole_file_lines: int = 400
    """If a fallback whole-file chunk would exceed this, we truncate."""

    min_chunk_lines: int = 1
    """Below this, neighbouring symbols may be merged. Currently unused
    (kept for the future merging implementation)."""

    include_module_preamble: bool = True


class SymbolAwareChunker:
    """Slice a parsed file into chunks at symbol boundaries.

    Construction takes optional config; everything else is pure.
    """

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._config = config or ChunkingConfig()

    # ------------------------------------------------------------------

    def chunk_parsed(self, parsed: ParsedFile, source: bytes) -> list[Chunk]:
        """Chunk a file we have a parse tree for. Preferred entry point."""
        text = _decode(source)
        lines = text.splitlines(keepends=True)

        # Functions / methods are the atomic chunkable units.
        callables: list[Symbol] = sorted(
            (s for s in parsed.symbols if s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)),
            key=lambda s: s.range.start_line,
        )

        if not callables:
            return [self._whole_file_chunk(parsed.file, text, lines)]

        out: list[Chunk] = []

        if self._config.include_module_preamble:
            preamble = self._module_preamble_chunk(parsed.file, lines, callables[0])
            if preamble is not None:
                out.append(preamble)

        for sym in callables:
            chunk = self._symbol_chunk(parsed.file, sym, lines)
            if chunk is not None:
                out.append(chunk)

        return out

    # ------------------------------------------------------------------

    def chunk(self, file: FileRef, source: bytes) -> Sequence[Chunk]:
        """Chunk without a parse tree.

        Used as a fallback for non-Python files or when the parser failed.
        Yields one chunk for the file (truncated to the line cap)."""
        text = _decode(source)
        return [self._whole_file_chunk(file, text, text.splitlines(keepends=True))]

    # ------------------------------------------------------------------

    def _symbol_chunk(
        self,
        file: FileRef,
        symbol: Symbol,
        lines: list[str],
    ) -> Chunk | None:
        start = max(0, symbol.range.start_line)
        end = min(len(lines), symbol.range.end_line + 1)
        if end <= start:
            return None
        body = "".join(lines[start:end])
        if not body.strip():
            return None
        return Chunk(
            id=f"{file.path.as_posix()}::{symbol.qualified_name}",
            file_path=file.path,
            text=body,
            start_line=start,
            end_line=end - 1,
            symbol_qname=symbol.qualified_name,
            language=file.language,
        )

    def _module_preamble_chunk(
        self,
        file: FileRef,
        lines: list[str],
        first_symbol: Symbol,
    ) -> Chunk | None:
        end = max(0, first_symbol.range.start_line)
        if end == 0:
            return None
        body = "".join(lines[:end])
        if not body.strip():
            return None
        return Chunk(
            id=f"{file.path.as_posix()}::__preamble__",
            file_path=file.path,
            text=body,
            start_line=0,
            end_line=end - 1,
            symbol_qname=None,
            language=file.language,
        )

    def _whole_file_chunk(
        self,
        file: FileRef,
        text: str,
        lines: list[str],
    ) -> Chunk:
        cap = self._config.max_whole_file_lines
        truncated = lines[:cap]
        body = "".join(truncated)
        return Chunk(
            id=f"{file.path.as_posix()}::__whole__",
            file_path=file.path,
            text=body,
            start_line=0,
            end_line=max(0, len(truncated) - 1),
            symbol_qname=None,
            language=file.language,
            metadata={"truncated": len(lines) > cap},
        )


def _decode(source: bytes) -> str:
    return source.decode("utf-8", errors="replace")


__all__ = ["ChunkingConfig", "SymbolAwareChunker"]
