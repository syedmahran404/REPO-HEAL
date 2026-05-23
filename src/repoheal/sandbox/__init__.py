"""Sandboxed command execution.

* :class:`SubprocessRunner` — runs commands as a child process with
  cwd, timeout, env scrubbing. Suitable for trusted local development
  and CI; **not** for arbitrary third-party code.
* :class:`DockerSandbox` — planned. Same Protocol, container-backed,
  no-network, dropped capabilities, resource-limited.
"""

from .runner import SubprocessRunner

__all__ = ["SubprocessRunner"]
