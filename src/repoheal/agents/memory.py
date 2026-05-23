"""MemoryBus — durable key/value store passed to every agent step.

Two backends:

* :class:`InMemoryBus` — dict-backed, used in tests and short-lived runs.
* :class:`JsonFileMemoryBus` — file-on-disk, autosaves on every put.
  Adequate for development; production will swap a Postgres-backed
  implementation behind the same Protocol.

The bus stores **JSON-shaped** values only. Agents that want to share
live references (graph, retrieval service) pass them in the per-step
``state`` dict, which is *not* persisted. Persistence is the unit of
durable execution: a re-run with the same ``run_id`` replays from the
persisted state.

We deliberately keep the API surface tiny — get / put / keys / snapshot
/ load. A larger surface (transactions, namespacing, watchers) is
exactly the kind of thing we'll regret committing to before we have a
distributed implementation in production.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryBus(Protocol):
    """Durable key/value store. JSON-shaped values only."""

    def get(self, key: str) -> Any | None: ...
    def put(self, key: str, value: Any) -> None: ...
    def keys(self) -> list[str]: ...
    def snapshot(self) -> dict[str, Any]: ...
    def load(self, snapshot: dict[str, Any]) -> None: ...


class InMemoryBus:
    """Dict-backed bus. Lifetime = the process."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def put(self, key: str, value: Any) -> None:
        # Round-trip through JSON to validate shape and to copy.
        self._store[key] = json.loads(json.dumps(value, default=_json_default))

    def keys(self) -> list[str]:
        return list(self._store)

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._store, default=_json_default))

    def load(self, snapshot: dict[str, Any]) -> None:
        self._store = dict(snapshot)


class JsonFileMemoryBus:
    """File-backed bus.

    Atomic writes via tempfile + replace so a crash mid-write never
    corrupts the file. Reads on first ``get`` if no snapshot has been
    loaded yet.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._loaded = False
        self._store: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        self._ensure_loaded()
        return self._store.get(key)

    def put(self, key: str, value: Any) -> None:
        self._ensure_loaded()
        self._store[key] = json.loads(json.dumps(value, default=_json_default))
        self._flush()

    def keys(self) -> list[str]:
        self._ensure_loaded()
        return list(self._store)

    def snapshot(self) -> dict[str, Any]:
        self._ensure_loaded()
        return json.loads(json.dumps(self._store, default=_json_default))

    def load(self, snapshot: dict[str, Any]) -> None:
        self._store = dict(snapshot)
        self._loaded = True
        self._flush()

    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if self._path.exists():
            try:
                self._store = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._store = {}
        else:
            self._store = {}
        self._loaded = True

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory, then atomic rename.
        fd, tmp_path = tempfile.mkstemp(
            prefix=self._path.name + ".",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._store, f, default=_json_default, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def _json_default(obj: Any) -> Any:
    """Fallback serializer for things JSON doesn't know about."""
    # Pydantic v2 models.
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, Path):
        return obj.as_posix()
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


__all__ = ["InMemoryBus", "JsonFileMemoryBus", "MemoryBus"]
