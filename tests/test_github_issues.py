"""Tests for the GitHub issue source.

We never hit the real API. Every test wires an :class:`httpx.MockTransport`
that returns canned responses based on the request URL.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from repoheal.issues import (
    GitHubIssueSource,
    IssueAuthError,
    IssueFetchError,
    IssueNotFoundError,
    IssueRateLimitError,
)


# --- fixture builders ----------------------------------------------------


def _issue_payload(
    *,
    number: int,
    title: str = "Bug",
    body: str = "Something is broken.",
    labels: list[str] | None = None,
    state: str = "open",
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": n} for n in (labels or [])],
        "state": state,
        "html_url": f"https://github.com/owner/repo/issues/{number}",
        "assignees": [{"login": "alice"}],
    }


def _comments_payload(*bodies: str) -> list[dict[str, Any]]:
    return [
        {"body": b, "user": {"login": f"user{i}"}, "created_at": "2026-05-23T00:00:00Z"}
        for i, b in enumerate(bodies)
    ]


def _make_source(handler: Callable[[httpx.Request], httpx.Response], *, token: str | None = "t-test") -> GitHubIssueSource:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return GitHubIssueSource(token=token, client=client)


# --- fetch happy paths ---------------------------------------------------


def test_fetch_returns_issue_with_body_and_labels() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/42"):
            return httpx.Response(200, json=_issue_payload(number=42, labels=["bug", "p2"]))
        if request.url.path.endswith("/issues/42/comments"):
            return httpx.Response(200, json=_comments_payload("first", "second"))
        return httpx.Response(404)

    with _make_source(handler) as src:
        issue = src.fetch("owner/repo", 42)

    assert issue.source == "github"
    assert issue.number == 42
    assert issue.title == "Bug"
    assert issue.body == "Something is broken."
    assert issue.labels == ("bug", "p2")
    assert issue.url == "https://github.com/owner/repo/issues/42"
    assert issue.metadata["state"] == "open"
    comments = issue.metadata["comments"]
    assert [c["body"] for c in comments] == ["first", "second"]


def test_fetch_handles_null_body() -> None:
    payload = _issue_payload(number=1)
    payload["body"] = None

    def handler(request: httpx.Request) -> httpx.Response:
        if "/comments" in request.url.path:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=payload)

    with _make_source(handler) as src:
        issue = src.fetch("o/r", 1)
    assert issue.body == ""


def test_fetch_handles_no_labels_or_comments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/comments" in request.url.path:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=_issue_payload(number=7, labels=[]))

    with _make_source(handler) as src:
        issue = src.fetch("o/r", 7)
    assert issue.labels == ()
    assert issue.metadata["comments"] == []


def test_fetch_sets_authorization_header() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        if "/comments" in request.url.path:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=_issue_payload(number=1))

    with _make_source(handler, token="ghp_xxx") as src:
        src.fetch("o/r", 1)

    assert captured.get("authorization") == "Bearer ghp_xxx"
    assert "github" in captured.get("accept", "")


def test_fetch_sends_no_authorization_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        if "/comments" in request.url.path:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=_issue_payload(number=1))

    with _make_source(handler, token=None) as src:
        src.fetch("o/r", 1)

    assert "authorization" not in captured


def test_fetch_picks_up_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        if "/comments" in request.url.path:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=_issue_payload(number=1))

    with _make_source(handler, token=None) as src:
        src.fetch("o/r", 1)

    assert captured.get("authorization") == "Bearer from-env"


# --- error mapping --------------------------------------------------------


def test_fetch_404_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with _make_source(handler) as src:
        with pytest.raises(IssueNotFoundError):
            src.fetch("o/r", 999)


def test_fetch_401_raises_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    with _make_source(handler) as src:
        with pytest.raises(IssueAuthError):
            src.fetch("o/r", 1)


def test_fetch_403_with_rate_limit_raises_rate_limit_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1764028800"},
            json={"message": "API rate limit exceeded"},
        )

    with _make_source(handler) as src:
        with pytest.raises(IssueRateLimitError) as exc_info:
            src.fetch("o/r", 1)
    assert exc_info.value.reset_at == 1764028800


def test_fetch_403_without_rate_limit_raises_generic_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Forbidden for this resource"})

    with _make_source(handler) as src:
        with pytest.raises(IssueFetchError) as exc_info:
            src.fetch("o/r", 1)
    # Must NOT be the rate-limit subclass.
    assert not isinstance(exc_info.value, IssueRateLimitError)


def test_fetch_500_raises_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with _make_source(handler) as src:
        with pytest.raises(IssueFetchError):
            src.fetch("o/r", 1)


# --- input validation -----------------------------------------------------


def test_fetch_rejects_invalid_repo_format() -> None:
    src = GitHubIssueSource(token="t", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    with pytest.raises(ValueError):
        src.fetch("not-a-slash-pair", 1)
    src.close()


# --- close-by-context ----------------------------------------------------


def test_context_manager_closes_owned_client() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json=[] if "/comments" in r.url.path else _issue_payload(number=1))
    )
    src = GitHubIssueSource(token="t", client=None)  # owns its client
    src._client = httpx.Client(transport=transport)  # type: ignore[assignment]
    with src as s:
        s.fetch("o/r", 1)
    # After exit, client should be closed; further calls raise.
    with pytest.raises(Exception):  # noqa: B017 — httpx raises various closed-client errors
        src._client.get("https://api.github.com/x")
