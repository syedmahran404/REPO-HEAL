"""Validation pipeline.

Orders validators from cheap to expensive (syntax, lint, type-check,
test, build) and short-circuits on the first ``FAIL``. ``WARN``s are
aggregated; the overall verdict is ``FAIL`` if any validator failed,
else ``WARN`` if any warned, else ``PASS``.

A validator that *itself* crashes is treated as ``ERROR``, which the
pipeline surfaces as ``FAIL`` (fail-closed).
"""

from __future__ import annotations

from collections.abc import Iterable

from ..core.models import (
    Patch,
    Repository,
    ValidationReport,
    ValidationResult,
    Verdict,
)
from ..core.protocols import Validator
from ..logging import get_logger

_log = get_logger(__name__)


class ValidationPipeline:
    """Run a sequence of validators against a patched repository."""

    def __init__(self, validators: Iterable[Validator]) -> None:
        self._validators: list[Validator] = list(validators)

    # ------------------------------------------------------------------

    def run(self, repo: Repository, patch: Patch) -> ValidationReport:
        results: list[ValidationResult] = []
        overall = Verdict.PASS

        for v in self._validators:
            try:
                result = v.validate(repo, patch)
            except Exception as exc:
                _log.warning(
                    "validation.validator_crashed",
                    validator=v.name,
                    error=str(exc),
                )
                result = ValidationResult(
                    validator=v.name,
                    verdict=Verdict.ERROR,
                    summary=f"validator crashed: {exc}",
                )
                results.append(result)
                overall = Verdict.FAIL  # fail-closed
                break

            results.append(result)
            _log.info(
                "validation.result",
                validator=v.name,
                verdict=result.verdict.value,
            )

            if result.verdict == Verdict.FAIL:
                overall = Verdict.FAIL
                break  # short-circuit: cheap validators fail before expensive ones run
            if result.verdict == Verdict.ERROR:
                overall = Verdict.FAIL
                break
            if result.verdict == Verdict.WARN and overall == Verdict.PASS:
                overall = Verdict.WARN

        return ValidationReport(overall=overall, results=tuple(results))
