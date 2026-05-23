"""Token-budget packing.

Given a ranked list of chunks and a token budget, decide which ones to
include. Greedy by score, but with a per-file cap so a single mega-class
can't dominate the context.

Token counts are estimated via :func:`tokenize.estimate_token_count`,
which is a 4-chars-per-token heuristic. We never feed this to a real
tokenizer; the goal is to avoid context-window overflows, and the
heuristic is conservative enough for that.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..core.models import Chunk
from .tokenize import estimate_token_count


class TokenBudgetPacker:
    """Pack chunks into a token budget."""

    def __init__(
        self,
        *,
        max_chunks_per_file: int = 4,
    ) -> None:
        self._max_per_file = max_chunks_per_file

    def pack(
        self,
        scored: Sequence[tuple[Chunk, float]],
        *,
        token_budget: int | None,
    ) -> list[Chunk]:
        out: list[Chunk] = []
        used = 0
        per_file: dict[Path, int] = {}

        for chunk, _score in scored:
            cost = estimate_token_count(chunk.text)
            count_for_file = per_file.get(chunk.file_path, 0)
            if count_for_file >= self._max_per_file:
                continue
            if token_budget is not None and used + cost > token_budget:
                # If the chunk itself is bigger than the whole budget,
                # there is no value in continuing — every remaining
                # chunk would also be skipped.
                if cost > token_budget and not out:
                    return out
                continue
            out.append(chunk)
            used += cost
            per_file[chunk.file_path] = count_for_file + 1
        return out


__all__ = ["TokenBudgetPacker"]
