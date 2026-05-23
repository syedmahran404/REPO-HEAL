"""Tests for the traceback parser and correlator."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoheal.analysis import AnalysisService
from repoheal.runtime import (
    ParsedTraceback,
    StackFrame,
    TracebackCorrelator,
    TracebackParser,
)


# --- TracebackParser ------------------------------------------------------


_STD_TRACEBACK = """\
Traceback (most recent call last):
  File "/tmp/repo/pkg/api.py", line 17, in public_endpoint
    return handle_request(user)
  File "/tmp/repo/pkg/services.py", line 13, in handle_request
    return user.name
AttributeError: 'NoneType' object has no attribute 'name'
"""


def test_parser_finds_all_frames() -> None:
    parsed = TracebackParser().parse(_STD_TRACEBACK)
    assert len(parsed.frames) == 2
    assert parsed.frames[0].function == "public_endpoint"
    assert parsed.frames[0].file.as_posix() == "/tmp/repo/pkg/api.py"
    assert parsed.frames[0].line == 17
    assert parsed.frames[1].function == "handle_request"
    assert parsed.frames[1].line == 13


def test_parser_extracts_exception_type_and_message() -> None:
    parsed = TracebackParser().parse(_STD_TRACEBACK)
    assert parsed.exception_type == "AttributeError"
    assert parsed.exception_message == "'NoneType' object has no attribute 'name'"


def test_parser_captures_code_context() -> None:
    parsed = TracebackParser().parse(_STD_TRACEBACK)
    assert parsed.frames[0].code == "return handle_request(user)"
    assert parsed.frames[1].code == "return user.name"


def test_parser_handles_no_traceback() -> None:
    parsed = TracebackParser().parse("there is no traceback here\n")
    assert parsed.frames == ()
    assert parsed.exception_type is None


def test_parser_handles_empty_input() -> None:
    parsed = TracebackParser().parse("")
    assert parsed.frames == ()


def test_parser_handles_chained_exceptions() -> None:
    text = """\
Traceback (most recent call last):
  File "a.py", line 5, in outer
    inner()
  File "b.py", line 9, in inner
    raise KeyError('x')
KeyError: 'x'

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "c.py", line 3, in caller
    outer()
RuntimeError: wrapped failure
"""
    parsed = TracebackParser().parse(text)
    # All five frames are surfaced in flat order.
    funcs = [f.function for f in parsed.frames]
    assert funcs == ["outer", "inner", "caller"]
    # The LAST exception line wins.
    assert parsed.exception_type == "RuntimeError"
    assert parsed.exception_message == "wrapped failure"


def test_parser_handles_dotted_exception_types() -> None:
    text = """\
Traceback (most recent call last):
  File "x.py", line 1, in <module>
    raise pkg.module.MyCustomError("bad")
pkg.module.MyCustomError: bad
"""
    parsed = TracebackParser().parse(text)
    assert parsed.exception_type == "pkg.module.MyCustomError"


def test_parser_tolerates_log_prefixed_lines() -> None:
    text = """\
2026-05-23T10:11:12 ERROR    File "/srv/app/x.py", line 42, in run
2026-05-23T10:11:12 ERROR        do_thing()
2026-05-23T10:11:12 ERROR    ValueError: bad
"""
    parsed = TracebackParser().parse(text)
    assert len(parsed.frames) == 1
    assert parsed.frames[0].file.as_posix() == "/srv/app/x.py"
    assert parsed.frames[0].line == 42
    assert parsed.frames[0].function == "run"


def test_parser_skips_module_level_when_no_func() -> None:
    text = """\
Traceback (most recent call last):
  File "/x.py", line 1
    bad syntax
SyntaxError: invalid syntax
"""
    parsed = TracebackParser().parse(text)
    assert parsed.frames[0].function == "<module>"
    assert parsed.exception_type == "SyntaxError"


# --- TracebackCorrelator -------------------------------------------------


pytestmark_correlator = pytest.importorskip("tree_sitter_languages")


def test_correlator_maps_frame_to_function(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    correlator = TracebackCorrelator(result.repository, result.graph)

    # Construct a synthetic traceback referring to handle_request.
    # The function spans roughly lines 14-16 (1-indexed) in the fixture;
    # line 16 is the ``return user.name`` body line.
    parsed = ParsedTraceback(
        frames=(
            StackFrame(
                file=Path("pkg/services.py"),
                line=16,
                function="handle_request",
            ),
        ),
        exception_type="ValueError",
        exception_message="boom",
    )
    correlated = correlator.correlate(parsed)
    assert correlated.primary_frame is not None
    assert correlated.primary_frame.qualified_name == "pkg.services.handle_request"
    # Function-name match → high confidence.
    assert correlated.primary_frame.confidence >= 0.9


def test_correlator_handles_absolute_traceback_path(medium_repo: Path) -> None:
    """A frame with an absolute path that ends with a known repo path
    must still be located via suffix matching."""
    result = AnalysisService().analyze(str(medium_repo))
    correlator = TracebackCorrelator(result.repository, result.graph)
    parsed = ParsedTraceback(
        frames=(
            StackFrame(
                file=Path("/tmp/builders/runtime/pkg/services.py"),
                line=16,
                function="handle_request",
            ),
        ),
    )
    correlated = correlator.correlate(parsed)
    assert correlated.primary_frame is not None
    assert correlated.primary_frame.qualified_name == "pkg.services.handle_request"
    assert correlated.primary_frame.file_in_repo == Path("pkg/services.py")


def test_correlator_marks_unknown_file_with_low_confidence(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    correlator = TracebackCorrelator(result.repository, result.graph)
    parsed = ParsedTraceback(
        frames=(
            StackFrame(file=Path("/site-packages/requests/api.py"), line=42, function="get"),
        ),
    )
    correlated = correlator.correlate(parsed)
    assert correlated.primary_frame is not None
    assert correlated.primary_frame.qualified_name is None
    assert correlated.primary_frame.confidence == 0.0


def test_correlator_returns_repo_files_unique_set(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    correlator = TracebackCorrelator(result.repository, result.graph)
    parsed = ParsedTraceback(
        frames=(
            StackFrame(file=Path("pkg/api.py"), line=10, function="public_endpoint"),
            StackFrame(file=Path("pkg/services.py"), line=18, function="handle_request"),
            StackFrame(file=Path("pkg/api.py"), line=11, function="public_endpoint"),
        ),
    )
    correlated = correlator.correlate(parsed)
    files = correlated.repo_files
    # api.py appears twice in frames but only once in repo_files.
    assert files == (Path("pkg/api.py"), Path("pkg/services.py"))


def test_correlator_handles_empty_traceback(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    correlator = TracebackCorrelator(result.repository, result.graph)
    correlated = correlator.correlate(ParsedTraceback())
    assert correlated.primary_frame is None
    assert correlated.repo_files == ()


def test_correlator_picks_smallest_containing_range(medium_repo: Path) -> None:
    """A nested function should win over its enclosing module."""
    result = AnalysisService().analyze(str(medium_repo))
    correlator = TracebackCorrelator(result.repository, result.graph)

    # A line *inside* handle_request, with a wrong function name on the
    # frame: the correlator should still land on handle_request because
    # of the line-range hit; confidence should be lower (no name match).
    parsed = ParsedTraceback(
        frames=(StackFrame(file=Path("pkg/services.py"), line=16, function="<unknown>"),),
    )
    correlated = correlator.correlate(parsed)
    assert correlated.primary_frame is not None
    assert correlated.primary_frame.qualified_name == "pkg.services.handle_request"
    assert 0.5 < correlated.primary_frame.confidence < 0.95
