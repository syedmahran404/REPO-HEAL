"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from repoheal.config import reset_settings_cache


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Force a fresh Settings instance per test."""
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def fixtures_root() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def tiny_repo(fixtures_root: Path) -> Path:
    """Tiny Python repo with a deliberate import cycle (Phase 1 fixture)."""
    return fixtures_root / "tiny_repo"


@pytest.fixture
def medium_repo(fixtures_root: Path) -> Path:
    """Larger Python repo exercising Phase 2 features:
    cross-module calls, inheritance, decorators, dead code, secrets,
    mutable defaults, broad except, unused imports, long methods, god class.
    """
    return fixtures_root / "medium_repo"
