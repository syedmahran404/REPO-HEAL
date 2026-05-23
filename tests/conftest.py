"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from repoheal.config import reset_settings_cache


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Force a fresh Settings instance per test (so monkeypatched env vars
    take effect without leaking between cases)."""
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def fixtures_root() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def tiny_repo(fixtures_root: Path) -> Path:
    """Path to the bundled tiny Python repo with a deliberate import cycle."""
    return fixtures_root / "tiny_repo"
