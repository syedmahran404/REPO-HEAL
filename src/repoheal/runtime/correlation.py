"""Map parsed stack frames to graph nodes.

Given a :class:`ParsedTraceback` and a repository's knowledge graph,
:class:`TracebackCorrelator` finds, for each frame, the closest
containing function/method/class node. Confidence depends on:

* whether the frame's file resolved to a known repo file at all;
* whether the line number lands inside a known symbol's range;
* whether the symbol's simple name matches the frame's function name.

Output: a :class:`CorrelatedTraceback`. The ``primary_frame`` property
is the innermost frame — the conventional "where the error happened"
location. Agents (e.g. :class:`RootCauseAgent`) can anchor on that
node and walk upstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core.models import Repository
from ..core.protocols import GraphBackend
from ..graph.schema import NodeKind, symbol_node_id
from .traceback_parser import ParsedTraceback, StackFrame


# Public domain models -----------------------------------------------------


class CorrelatedFrame(BaseModel):
    """One :class:`StackFrame` mapped (or not) to a graph node."""

    model_config = ConfigDict(frozen=True)

    frame: StackFrame
    node_id: str | None = None
    qualified_name: str | None = None
    file_in_repo: Path | None = None  # frame.file resolved to a repo-relative path
    confidence: float = 0.0  # in [0, 1]


class CorrelatedTraceback(BaseModel):
    """A parsed traceback with graph correlations attached to each frame."""

    model_config = ConfigDict(frozen=True)

    traceback: ParsedTraceback
    frames: tuple[CorrelatedFrame, ...]

    @property
    def primary_frame(self) -> CorrelatedFrame | None:
        """Last frame — most recent call. Returns ``None`` for empty
        tracebacks."""
        return self.frames[-1] if self.frames else None

    @property
    def repo_files(self) -> tuple[Path, ...]:
        """The unique set of repo-relative files referenced by the frames."""
        seen: set[Path] = set()
        out: list[Path] = []
        for cf in self.frames:
            if cf.file_in_repo is not None and cf.file_in_repo not in seen:
                seen.add(cf.file_in_repo)
                out.append(cf.file_in_repo)
        return tuple(out)


# --- Internal index ------------------------------------------------------


@dataclass(frozen=True)
class _SymbolEntry:
    start_line: int  # 0-indexed (graph convention)
    end_line: int    # 0-indexed
    node_id: str
    qualified_name: str
    kind: str
    name: str


class TracebackCorrelator:
    """Correlate frames to graph nodes for a single repo+graph pair.

    Construction is O(N) over graph nodes; use one correlator per
    analyse-and-correlate session."""

    def __init__(self, repo: Repository, graph: GraphBackend) -> None:
        self._repo = repo
        self._graph = graph
        self._index = self._build_index()

    # ------------------------------------------------------------------

    def correlate(self, parsed: ParsedTraceback) -> CorrelatedTraceback:
        out: list[CorrelatedFrame] = []
        for frame in parsed.frames:
            file_in_repo = self._normalise_path(frame.file)
            out.append(self._correlate_frame(frame, file_in_repo))
        return CorrelatedTraceback(traceback=parsed, frames=tuple(out))

    # ------------------------------------------------------------------

    def _build_index(self) -> dict[Path, list[_SymbolEntry]]:
        index: dict[Path, list[_SymbolEntry]] = {}
        for node_id in self._graph.all_nodes():
            attrs = self._safe_attrs(node_id)
            if not attrs:
                continue
            kind = attrs.get("kind")
            if kind not in (
                NodeKind.FUNCTION.value,
                NodeKind.METHOD.value,
                NodeKind.CLASS.value,
                NodeKind.MODULE.value,
            ):
                continue
            file = attrs.get("file")
            start = attrs.get("start_line")
            end = attrs.get("end_line")
            qname = attrs.get("qualified_name") or node_id
            name = attrs.get("name") or qname.rsplit(".", 1)[-1]
            if not isinstance(file, str) or not isinstance(start, int) or not isinstance(end, int):
                continue
            entry = _SymbolEntry(
                start_line=start,
                end_line=end,
                node_id=node_id,
                qualified_name=qname,
                kind=kind,
                name=name,
            )
            index.setdefault(Path(file), []).append(entry)

        # Sort each file's entries so the smallest containing range is
        # easy to pick up later (smaller-span first wins on tie).
        for entries in index.values():
            entries.sort(key=lambda e: (e.start_line, e.end_line - e.start_line))
        return index

    def _normalise_path(self, frame_path: Path) -> Path | None:
        """Try to express a (possibly absolute / virtual) traceback path
        as a path relative to the repo root."""
        repo_root = self._repo.root.resolve()
        # 1. Already relative-clean?
        try:
            return frame_path.relative_to(repo_root)
        except ValueError:
            pass

        # 2. If it's absolute, attempt resolve + relative-to.
        if frame_path.is_absolute():
            try:
                return frame_path.resolve().relative_to(repo_root)
            except (OSError, ValueError):
                pass

        # 3. Suffix match: the frame path ends with one of our known
        # repo files. Common case: traceback prints "/tmp/build/x/pkg/a.py"
        # and the repo has "pkg/a.py" — we match by trailing segments.
        frame_posix = frame_path.as_posix().lstrip("./")
        for known in self._index:
            known_posix = known.as_posix()
            if frame_posix.endswith("/" + known_posix) or frame_posix == known_posix:
                return known
        # Try the inverse — known file's tail matches the frame.
        for known in self._index:
            if known.as_posix().endswith(frame_path.name):
                # Weak match on basename only — usable but lower confidence.
                if frame_path.name and frame_path.name == known.name:
                    return known
        return None

    def _correlate_frame(
        self,
        frame: StackFrame,
        file_in_repo: Path | None,
    ) -> CorrelatedFrame:
        if file_in_repo is None:
            return CorrelatedFrame(frame=frame, file_in_repo=None, confidence=0.0)

        entries = self._index.get(file_in_repo, [])
        if not entries:
            return CorrelatedFrame(frame=frame, file_in_repo=file_in_repo, confidence=0.3)

        # Tracebacks are 1-indexed; graph ranges are 0-indexed.
        target = frame.line - 1

        # Find the smallest containing range. Module-level entries
        # (kind=MODULE) have wide ranges; we still consider them, but
        # prefer narrower hits.
        candidates: list[_SymbolEntry] = [
            e for e in entries if e.start_line <= target <= e.end_line
        ]
        if not candidates:
            return CorrelatedFrame(frame=frame, file_in_repo=file_in_repo, confidence=0.4)

        # Smallest-range first — that's the deepest scope.
        candidates.sort(key=lambda e: e.end_line - e.start_line)
        best = candidates[0]

        confidence = 0.7
        # Boost when the function name matches the frame's function.
        if frame.function and frame.function != "<module>":
            simple = frame.function.split(".")[-1]
            if best.name == simple or best.qualified_name.endswith(f".{simple}"):
                confidence = 0.95
        elif frame.function == "<module>" and best.kind == NodeKind.MODULE.value:
            confidence = 0.9

        return CorrelatedFrame(
            frame=frame,
            file_in_repo=file_in_repo,
            node_id=best.node_id,
            qualified_name=best.qualified_name,
            confidence=confidence,
        )

    def _safe_attrs(self, node_id: str) -> dict[str, Any] | None:
        try:
            return self._graph.node_attrs(node_id)
        except Exception:
            return None


__all__ = [
    "CorrelatedFrame",
    "CorrelatedTraceback",
    "TracebackCorrelator",
]
