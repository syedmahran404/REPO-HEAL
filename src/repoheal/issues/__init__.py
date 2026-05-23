"""Issue ingestion subsystem.

Phase 1 shipped Protocol + LocalIssueSource (test-only).

Phase 2 ships:

* :class:`GitHubIssueSource` — real GitHub REST API client over httpx,
  with the typed error hierarchy ``IssueFetchError`` /
  ``IssueNotFoundError`` / ``IssueAuthError`` / ``IssueRateLimitError``.
* :class:`IssueCorrelator` — turns an :class:`Issue` into an
  :class:`IssueCorrelation` carrying parsed-and-correlated tracebacks
  and retrieval-derived candidate files.

GitLab and PR-creation adapters are the next milestones.
"""

from .correlate import IssueCorrelation, IssueCorrelator
from .github import (
    GitHubIssueSource,
    IssueAuthError,
    IssueFetchError,
    IssueNotFoundError,
    IssueRateLimitError,
)
from .source import LocalIssueSource

__all__ = [
    "GitHubIssueSource",
    "IssueAuthError",
    "IssueCorrelation",
    "IssueCorrelator",
    "IssueFetchError",
    "IssueNotFoundError",
    "IssueRateLimitError",
    "LocalIssueSource",
]
