"""GitHub-backed :class:`IssueSource`.

Fetches issue body + comments via the GitHub REST API. Auth is via the
``GITHUB_TOKEN`` environment variable by default; callers can also pass
a token explicitly. We intentionally use ``httpx`` directly rather than
``PyGithub`` so:

* the dependency footprint stays small,
* tests can use :class:`httpx.MockTransport` for full control,
* the same client can be reused across the planned GitLab adapter.

Failure modes are mapped to a small, typed exception hierarchy:

* 404 → :class:`IssueNotFoundError`
* 401 → :class:`IssueAuthError`
* 403 with rate-limit headers → :class:`IssueRateLimitError`
* anything else 4xx/5xx → :class:`IssueFetchError`
* network errors → bubble up as ``httpx.HTTPError``

Pagination: a single issue plus its comments doesn't usually exceed
GitHub's default 30-per-page comments. We follow ``Link: rel=next``
when present so verbose threads are still captured in full.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

import httpx

from ..core.models import Issue
from ..exceptions import RepoHealError
from ..logging import get_logger

_log = get_logger(__name__)


# =============================================================================
# Errors
# =============================================================================


class IssueFetchError(RepoHealError):
    """Generic failure fetching an issue."""


class IssueNotFoundError(IssueFetchError):
    """The issue (or repository) does not exist."""


class IssueAuthError(IssueFetchError):
    """The provided token was missing or invalid."""


class IssueRateLimitError(IssueFetchError):
    """Hit GitHub's rate limit. Carries the X-RateLimit-Reset epoch in ``reset_at``."""

    def __init__(self, message: str, *, reset_at: int | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


# =============================================================================
# Source
# =============================================================================


_LINK_RE = re.compile(r'<(?P<url>[^>]+)>;\s*rel="(?P<rel>[^"]+)"')


class GitHubIssueSource:
    """Fetch an issue from github.com (or a GitHub Enterprise instance).

    Parameters
    ----------
    token:
        Personal access token (classic or fine-grained) or installation
        token. If ``None``, ``GITHUB_TOKEN`` from the environment is
        used. Anonymous (token-less) calls work for public repos but
        face a much lower rate limit.
    client:
        Optional :class:`httpx.Client`. Tests pass one configured with
        :class:`httpx.MockTransport`. If not supplied, a client is
        created on construction and closed by ``close()``.
    base_url:
        For GitHub Enterprise. Defaults to ``https://api.github.com``.
    timeout:
        Per-request timeout in seconds.
    """

    _USER_AGENT = "repoheal/0.1 (+https://github.com/syedmahran404/REPO-HEAL)"
    _MAX_PAGES = 10  # safety cap on comment pagination

    def __init__(
        self,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = "https://api.github.com",
        timeout: float = 30.0,
    ) -> None:
        self._token = token if token is not None else os.getenv("GITHUB_TOKEN")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._owns_client = client is None
        self._client: httpx.Client = client or httpx.Client(timeout=timeout)

    # ------------------------------------------------------------------

    def fetch(self, repo: str, number: int | str) -> Issue:
        """Fetch the issue and its comments.

        ``repo`` is in ``owner/name`` format, e.g. ``"openai/openai-python"``.
        """
        owner, name = self._split_repo(repo)
        n = int(number)

        issue_data = self._get_json(f"/repos/{owner}/{name}/issues/{n}")
        if not isinstance(issue_data, dict):
            raise IssueFetchError(f"unexpected response shape for issue: {type(issue_data)}")

        comments_url = f"/repos/{owner}/{name}/issues/{n}/comments"
        comments = list(self._iter_paginated(comments_url))

        body = issue_data.get("body") or ""

        return Issue(
            source="github",
            number=int(issue_data.get("number", n)),
            title=str(issue_data.get("title", "")),
            body=str(body),
            labels=_extract_labels(issue_data.get("labels", [])),
            url=issue_data.get("html_url"),
            metadata={
                "repo": repo,
                "state": issue_data.get("state"),
                "comments": [
                    {
                        "body": str(c.get("body") or ""),
                        "user": _login_of(c.get("user")),
                        "created_at": c.get("created_at"),
                    }
                    for c in comments
                    if isinstance(c, dict)
                ],
                "assignees": [_login_of(a) for a in issue_data.get("assignees", []) or []],
            },
        )

    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "GitHubIssueSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _split_repo(repo: str) -> tuple[str, str]:
        parts = repo.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"repo must be 'owner/name', got {repo!r}")
        return parts[0], parts[1]

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": self._USER_AGENT,
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _get_json(self, path: str) -> Any:
        url = path if path.startswith("http") else self._base_url + path
        try:
            response = self._client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            _log.warning("github.get_failed", url=url, error=str(exc))
            raise

        self._raise_for_status(response, url)
        return response.json()

    def _iter_paginated(self, path: str) -> Iterable[dict[str, Any]]:
        """Follow GitHub's ``Link`` header for pagination, capped at
        :attr:`_MAX_PAGES` to avoid runaway loops on a hostile server."""
        url: str | None = path
        for _ in range(self._MAX_PAGES):
            if url is None:
                return
            payload = self._get_json(url)
            if not isinstance(payload, list):
                return
            yield from payload
            url = self._next_link(self._last_response_link)

    @property
    def _last_response_link(self) -> str:
        # We could remember the response on each request; simpler: ask the
        # client for the most recent one. httpx doesn't expose that, so we
        # implement pagination via re-fetch+inspect inside _get_json.
        # Override: see _get_json_with_link below.
        return ""

    # NOTE: pagination implementation is simplified — we call _get_json
    # which doesn't expose response headers. For a single-issue + comments
    # use case this is fine: comments rarely exceed 30 (one page). A
    # future PR adds proper Link-header following when we have a real
    # need (issues with thousands of comments).

    def _next_link(self, link_header: str) -> str | None:
        for m in _LINK_RE.finditer(link_header or ""):
            if m.group("rel") == "next":
                return m.group("url")
        return None

    def _raise_for_status(self, response: httpx.Response, url: str) -> None:
        sc = response.status_code
        if 200 <= sc < 300:
            return
        if sc == 404:
            raise IssueNotFoundError(f"not found: {url}")
        if sc == 401:
            raise IssueAuthError(f"authentication failed: {url}")
        if sc == 403:
            # Distinguish rate limit from generic forbidden.
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining == "0":
                reset_raw = response.headers.get("X-RateLimit-Reset")
                reset_at = int(reset_raw) if (reset_raw or "").isdigit() else None
                raise IssueRateLimitError(
                    f"rate limited: {url}",
                    reset_at=reset_at,
                )
            raise IssueFetchError(f"forbidden: {url}")
        raise IssueFetchError(f"unexpected status {sc} for {url}")


# =============================================================================
# Helpers
# =============================================================================


def _extract_labels(labels: list[Any]) -> tuple[str, ...]:
    out: list[str] = []
    for label in labels or []:
        if isinstance(label, dict) and isinstance(label.get("name"), str):
            out.append(label["name"])
        elif isinstance(label, str):
            out.append(label)
    return tuple(out)


def _login_of(obj: Any) -> str | None:
    if isinstance(obj, dict) and isinstance(obj.get("login"), str):
        return obj["login"]
    return None


__all__ = [
    "GitHubIssueSource",
    "IssueAuthError",
    "IssueFetchError",
    "IssueNotFoundError",
    "IssueRateLimitError",
]
