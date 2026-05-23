"""Base data types for agents."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class AgentStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    NEEDS_INPUT = "needs_input"  # the agent decided it can't proceed without more info


class AgentResult(BaseModel):
    """The terminal value of a single agent run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: UUID = Field(default_factory=uuid4)
    agent: str
    status: AgentStatus
    summary: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    errors: tuple[str, ...] = ()
