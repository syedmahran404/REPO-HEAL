"""Application configuration loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration.

    Loaded from environment variables prefixed ``REPOHEAL_`` and from a
    ``.env`` file at the project root if present. Values are validated by
    pydantic and accessed via :func:`get_settings`.
    """

    model_config = SettingsConfigDict(
        env_prefix="REPOHEAL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General -----------------------------------------------------------
    workdir: Path = Field(
        default=Path(".repoheal"),
        description="Where snapshots, graph dumps, and per-job artifacts live.",
    )

    # --- Logging -----------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "console"

    # --- Ingestion --------------------------------------------------------
    git_timeout_seconds: int = 300
    max_file_size_bytes: int = 2 * 1024 * 1024  # 2 MiB

    # --- Sandbox ----------------------------------------------------------
    sandbox_backend: Literal["subprocess", "docker"] = "subprocess"
    sandbox_default_timeout: int = 60

    # --- Graph -------------------------------------------------------------
    graph_backend: Literal["networkx", "neo4j"] = "networkx"

    def ensure_workdir(self) -> Path:
        """Create the working directory if it doesn't exist; return it."""
        self.workdir.mkdir(parents=True, exist_ok=True)
        return self.workdir


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    Cached for the life of the process. Tests that need to vary settings
    should call :func:`reset_settings_cache` between cases.
    """
    return Settings()


def reset_settings_cache() -> None:
    """Reset the lru_cache for ``get_settings`` (test-only helper)."""
    get_settings.cache_clear()
