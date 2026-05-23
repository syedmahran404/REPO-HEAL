"""Structured logging setup using ``structlog``.

Two output modes:
* ``console`` — human-readable, colored, key-value, intended for dev.
* ``json``    — single-line JSON, intended for log shippers.

Configure once via :func:`configure_logging` at process start.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

from .config import Settings, get_settings


def configure_logging(settings: Settings | None = None) -> None:
    """Configure stdlib logging and structlog.

    Idempotent: safe to call multiple times.
    """
    cfg = settings or get_settings()
    level = getattr(logging, cfg.log_level)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor
    if cfg.log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None, **initial: Any) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. Call ``configure_logging`` once before using."""
    log = structlog.get_logger(name) if name else structlog.get_logger()
    if initial:
        log = log.bind(**initial)
    return log  # type: ignore[no-any-return]
