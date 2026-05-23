"""Tests for IssueCorrelator — issue → traceback frames + candidate files."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoheal.analysis import AnalysisService
from repoheal.core.models import Issue
from repoheal.issues import IssueCorrelator

pytest.importorskip("tree_sitter_languages")


_TRACEBACK = """\
Traceback (most recent call last):
  File "pkg/api.py", line 16, in public_endpoint
    user = make_admin(name)
  File "pkg/services.py", line 16, in handle_request
    return user.name
AttributeError: 'NoneType' object has no attribute 'name'
"""


def _issue(body: str, comments: list[str] | None = None) -> Issue:
    return Issue(
        source="github",
        number=1,
        title="Bug in handle_request",
        body=body,
        labels=("bug",),
        url="https://example/1",
        metadata={
            "comments": [{"body": c, "user": "alice"} for c in (comments or [])],
        },
    )


def test_correlator_extracts_traceback_from_body(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    correlator = IssueCorrelator(result.repository, result.graph)

    issue = _issue(body=_TRACEBACK)
    out = correlator.correlate(issue)

    assert len(out.correlated_tracebacks) == 1
    primary = out.correlated_tracebacks[0].primary_frame
    assert primary is not None
    assert primary.qualified_name == "pkg.services.handle_request"


def test_correlator_extracts_traceback_from_comments(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    correlator = IssueCorrelator(result.repository, result.graph)

    # Body has prose only; the traceback lives in a follow-up comment.
    issue = _issue(
        body="Here is some context, no traceback yet.",
        comments=[_TRACEBACK],
    )
    out = correlator.correlate(issue)

    assert len(out.correlated_tracebacks) == 1


def test_correlator_handles_issue_without_traceback(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    correlator = IssueCorrelator(result.repository, result.graph)

    out = correlator.correlate(_issue(body="No code in this issue."))
    assert out.correlated_tracebacks == ()


def test_correlator_finds_candidate_files_via_retrieval(medium_repo: Path) -> None:
    """When retrieval is wired in, the correlator returns candidate
    files even if there's no traceback."""
    result = AnalysisService().analyze(str(medium_repo), build_retrieval=True)
    correlator = IssueCorrelator(
        result.repository,
        result.graph,
        retrieval=result.retrieval,
    )

    issue = _issue(
        body="The handle_request function returns the wrong user name when the user is None.",
    )
    out = correlator.correlate(issue)

    assert out.candidate_files, "expected at least one candidate file"
    # services.py contains handle_request; should be in the candidates.
    assert Path("pkg/services.py") in out.candidate_files


def test_correlator_traceback_files_appear_first_in_candidates(medium_repo: Path) -> None:
    """A file referenced by a traceback must be ranked above plain
    retrieval matches in the candidate_files list."""
    result = AnalysisService().analyze(str(medium_repo), build_retrieval=True)
    correlator = IssueCorrelator(
        result.repository,
        result.graph,
        retrieval=result.retrieval,
    )

    issue = _issue(body=_TRACEBACK)
    out = correlator.correlate(issue)

    # services.py is in the traceback. It should be at index 0 (or among
    # the first entries) regardless of what retrieval returns.
    assert out.candidate_files[0] in (Path("pkg/services.py"), Path("pkg/api.py"))


def test_correlator_primary_anchor_node(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    correlator = IssueCorrelator(result.repository, result.graph)

    out = correlator.correlate(_issue(body=_TRACEBACK))
    anchor = out.primary_anchor_node
    assert isinstance(anchor, str) and anchor.startswith("function::")


def test_correlator_primary_anchor_none_when_no_tb(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))
    correlator = IssueCorrelator(result.repository, result.graph)

    out = correlator.correlate(_issue(body="Just text."))
    assert out.primary_anchor_node is None


def test_correlator_works_without_retrieval(medium_repo: Path) -> None:
    result = AnalysisService().analyze(str(medium_repo))  # no retrieval
    correlator = IssueCorrelator(result.repository, result.graph, retrieval=None)
    out = correlator.correlate(_issue(body=_TRACEBACK))
    # Tracebacks still extracted; candidate files come from the traceback only.
    assert out.correlated_tracebacks != ()
    assert Path("pkg/services.py") in out.candidate_files
