"""Issue ingestion — INTERFACE-DEFINED ONLY in Phase 1.

* :class:`IssueSource` Protocol — how the system fetches an issue.
* :class:`Issue` model — already in :mod:`repoheal.core.models`.

A GitHub-backed implementation is the next milestone here. It will use
PyGithub or the REST API directly with token auth scoped to the
target repository, and will hydrate an :class:`Issue` with its body,
labels, and any stack traces parseable from comments.
"""

from .source import LocalIssueSource

__all__ = ["LocalIssueSource"]
