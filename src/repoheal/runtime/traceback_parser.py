"""Python traceback parser.

Handles:

* The standard ``Traceback (most recent call last):`` format.
* Tracebacks embedded in log output (with timestamps / log-level
  prefixes on every line).
* Chained exceptions (``During handling of...`` / ``The above
  exception was the direct cause of...``); we keep all frames.
* The optional source-context line under each ``File "..."``,
  surfaced as :attr:`StackFrame.code`.

It does **not** attempt to be a fully faithful reproduction of
:mod:`traceback` — we want a structured handle to the (file, line,
function) tuples and the exception type/message for downstream
analysis. Edge cases like ``ExceptionGroup`` from PEP 654 are reduced
to a flat frame list; the wrapping group is preserved as the
:attr:`ParsedTraceback.exception_type`.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class StackFrame(BaseModel):
    """One ``File "..." line N, in function`` entry from a traceback."""

    model_config = ConfigDict(frozen=True)

    file: Path  # may be absolute; correlation handles repo-relative mapping
    line: int  # 1-indexed, matching the traceback convention
    function: str  # ``<module>`` for module-level frames
    code: str | None = None  # the source line shown below the frame, if present


class ParsedTraceback(BaseModel):
    """Result of parsing one traceback chunk."""

    model_config = ConfigDict(frozen=True)

    frames: tuple[StackFrame, ...] = Field(default_factory=tuple)
    exception_type: str | None = None
    exception_message: str | None = None


# Tolerates leading whitespace (logs often indent stack lines) and
# colon-prefixes (``levelname:logger:File "...", line ...``).
_FRAME_RE = re.compile(
    r'\bFile\s+"(?P<file>[^"\n]+)",\s+line\s+(?P<line>\d+)'
    r'(?:,\s+in\s+(?P<func>[^\n]+))?'
)

# Last-line exception detector. Python's traceback module ends with
# ``QualifiedName: message``. The qualifier accepts dotted names so
# things like ``pkg.module.SomeError`` work — we don't require the head
# to be capitalised because real Python module names start lowercase.
_EXCEPTION_RE = re.compile(
    r"^(?P<type>[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*)*)"
    r"(?::\s*(?P<msg>.*))?\s*$"
)


class TracebackParser:
    """Parse Python tracebacks (and log-embedded ones)."""

    def parse(self, text: str) -> ParsedTraceback:
        """Parse ``text`` and return a :class:`ParsedTraceback`.

        Always succeeds; if no frames are found the ``frames`` tuple is
        empty. Garbage in → empty out, never an exception."""
        if not text:
            return ParsedTraceback()

        lines = text.splitlines()
        frames: list[StackFrame] = []

        i = 0
        while i < len(lines):
            line = lines[i]
            match = _FRAME_RE.search(line)
            if match is None:
                i += 1
                continue

            file_path = match.group("file")
            line_no = int(match.group("line"))
            func = (match.group("func") or "<module>").strip()

            # Peek at the next line for the source-context. Python
            # indents it more than the ``File "..."`` line. We require
            # the next line to look indented and not start a new frame.
            code: str | None = None
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                stripped = nxt.strip()
                if (
                    stripped
                    and not _FRAME_RE.search(nxt)
                    and not stripped.startswith("Traceback")
                    and not _EXCEPTION_RE.match(stripped)
                ):
                    # Heuristic: indented vs. flush-left. Tolerate log
                    # prefixes by checking if the frame line itself was
                    # log-prefixed and use the same prefix length.
                    if nxt.startswith((" ", "\t")):
                        code = stripped
                        i += 1

            frames.append(
                StackFrame(
                    file=Path(file_path),
                    line=line_no,
                    function=func,
                    code=code,
                )
            )
            i += 1

        ex_type, ex_msg = _detect_exception(lines, frames)

        return ParsedTraceback(
            frames=tuple(frames),
            exception_type=ex_type,
            exception_message=ex_msg,
        )


def _detect_exception(lines: list[str], frames: list[StackFrame]) -> tuple[str | None, str | None]:
    """Look at the lines AFTER the last frame for the exception type/message.

    Python's traceback prints frames first, then a blank/separator,
    then ``ExceptionType: message``. We scan backward from the end and
    take the *last* match — chained exceptions emit several, and the
    final one is what surfaced to the user.
    """
    if not frames:
        # Without frames, scan everything for the exception line.
        candidates = lines
    else:
        # Without precise byte offsets, scanning the entire input is
        # fine; the regex won't match indented frame-context lines
        # because we anchor on a capitalised-leading-letter type.
        candidates = lines

    for line in reversed(candidates):
        stripped = line.strip()
        if not stripped:
            continue
        if "Traceback (most recent call last)" in stripped:
            continue
        # Skip lines that are part of a continuation message.
        if stripped.startswith("During handling") or stripped.startswith("The above"):
            continue
        m = _EXCEPTION_RE.match(stripped)
        if m is None:
            continue
        # Drop matches that are obviously not exception lines (e.g. an
        # uppercase-starting code constant). The strongest signal is
        # the type ending in ``Error``, ``Warning``, or ``Exception``.
        type_name = m.group("type")
        last = type_name.rsplit(".", 1)[-1]
        if not (
            last.endswith("Error")
            or last.endswith("Warning")
            or last == "Exception"
            or last == "BaseException"
            or last.endswith("Exit")
            or last == "StopIteration"
            or last == "KeyboardInterrupt"
            or last == "SystemExit"
        ):
            continue
        msg = m.group("msg") or None
        return type_name, msg

    return None, None


__all__ = ["ParsedTraceback", "StackFrame", "TracebackParser"]
