"""Subprocess sandbox runner.

This is the dev-mode sandbox. It enforces:

* ``cwd`` so commands never escape the repository root;
* a wallclock timeout with hard kill on expiry;
* an env scrubbing pass that, by default, only forwards a small
  whitelist of variables (``PATH``, ``HOME``, ``LANG``, ``LC_*``).

It does **not** isolate the filesystem, the network, or the user
namespace. A Docker-backed runner will, behind the same Protocol.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from ..config import Settings, get_settings
from ..core.protocols import SandboxResult
from ..exceptions import SandboxError, SandboxTimeoutError
from ..logging import get_logger

_log = get_logger(__name__)

# Variables we forward by default. Anything not on this list is dropped.
_DEFAULT_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_COLLATE",
        "LC_TIME",
        "LC_NUMERIC",
        "LC_MONETARY",
        "TZ",
        # Useful for tools that want a temp dir.
        "TMPDIR",
        "TMP",
        "TEMP",
    }
)


class SubprocessRunner:
    """Implementation of :class:`SandboxRunner` using :mod:`subprocess`."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        env_allowlist: frozenset[str] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._env_allowlist = env_allowlist or _DEFAULT_ENV_ALLOWLIST

    # ------------------------------------------------------------------

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> SandboxResult:
        if not command:
            raise SandboxError("empty command")
        if not cwd.exists() or not cwd.is_dir():
            raise SandboxError(f"cwd does not exist: {cwd}")

        effective_timeout = timeout or self._settings.sandbox_default_timeout
        effective_env = self._build_env(env)

        _log.debug(
            "sandbox.run",
            command=list(command),
            cwd=str(cwd),
            timeout=effective_timeout,
        )

        start = time.monotonic()
        try:
            # Use start_new_session so a kill targets the whole group,
            # cleaning up any subprocesses the command spawned.
            proc = subprocess.Popen(  # noqa: S603 - controlled cmd list
                list(command),
                cwd=str(cwd),
                env=effective_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise SandboxError(f"command not found: {command[0]}") from exc

        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = b"", b""

        duration = time.monotonic() - start

        if timed_out:
            raise SandboxTimeoutError(
                f"command timed out after {effective_timeout}s: {' '.join(command)}"
            )

        return SandboxResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            timed_out=False,
        )

    # ------------------------------------------------------------------

    def _build_env(self, overrides: dict[str, str] | None) -> dict[str, str]:
        env: dict[str, str] = {}
        for k, v in os.environ.items():
            if k in self._env_allowlist:
                env[k] = v
        if overrides:
            env.update(overrides)
        return env


# --- helpers --------------------------------------------------------------


def _kill_group(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    # Give it a moment, then SIGKILL the survivors.
    time.sleep(0.5)
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
