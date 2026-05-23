"""Issue sources.

:class:`LocalIssueSource` reads issue bodies from on-disk JSON files for
testing the agent pipeline without network access. The GitHub
implementation is the next milestone; the Protocol is identical.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.models import Issue
from ..exceptions import RepoHealError


class LocalIssueSource:
    """Read issues from a local directory of JSON files.

    Each file is expected to contain ``{"number": …, "title": …,
    "body": …, ...}``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def fetch(self, repo: str, number: int | str) -> Issue:
        path = self._root / f"{number}.json"
        if not path.exists():
            raise RepoHealError(f"issue file not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RepoHealError(f"could not load issue {path}: {exc}") from exc

        return Issue(
            source="local",
            number=data.get("number", number),
            title=str(data.get("title", "")),
            body=str(data.get("body", "")),
            labels=tuple(data.get("labels", ())),
            url=data.get("url"),
            metadata={"repo": repo, **data.get("metadata", {})},
        )
