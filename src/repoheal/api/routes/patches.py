"""Patch ranking endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...analysis import AnalysisService
from ...core.models import (
    FileEdit,
    Patch,
    ValidationReport,
    ValidationResult,
    Verdict,
)
from ...exceptions import IngestionError, RepoHealError
from ...patching import PatchCandidate, PatchRanker, RankingContext

router = APIRouter(prefix="/patches", tags=["patches"])


class _EditInput(BaseModel):
    file: str
    new_content: str
    is_new_file: bool = False
    is_deletion: bool = False


class _PatchInput(BaseModel):
    title: str = "candidate"
    description: str = ""
    edits: list[_EditInput] = Field(default_factory=list)


class _ValidationInput(BaseModel):
    overall: Verdict
    summary: str = ""


class _CandidateInput(BaseModel):
    patch: _PatchInput
    validation: _ValidationInput | None = None


class RankRequest(BaseModel):
    path: str
    candidates: list[_CandidateInput] = Field(default_factory=list, min_length=0)


@router.post("/rank")
def rank(req: RankRequest, request: Request) -> dict[str, Any]:
    if not req.candidates:
        return {"ranked": []}

    service: AnalysisService = request.app.state.analysis
    try:
        analysis = service.analyze(req.path)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RepoHealError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    candidates = [_to_candidate(c) for c in req.candidates]
    ctx = RankingContext(repo=analysis.repository, graph=analysis.graph)
    ranked = PatchRanker().rank(candidates, ctx)

    return {
        "ranked": [
            {
                "patch_id": str(score.patch_id),
                "patch_title": cand.patch.title,
                "composite": score.composite,
                "breakdown": score.breakdown,
            }
            for cand, score in ranked
        ]
    }


def _to_candidate(c: _CandidateInput) -> PatchCandidate:
    edits = tuple(
        FileEdit(
            file=Path(e.file),
            new_content=e.new_content,
            is_new_file=e.is_new_file,
            is_deletion=e.is_deletion,
        )
        for e in c.patch.edits
    )
    patch = Patch(title=c.patch.title, description=c.patch.description, edits=edits)
    validation: ValidationReport | None = None
    if c.validation is not None:
        validation = ValidationReport(
            overall=c.validation.overall,
            results=(
                ValidationResult(
                    validator="external",
                    verdict=c.validation.overall,
                    summary=c.validation.summary,
                ),
            ),
        )
    return PatchCandidate(patch=patch, validation=validation)
