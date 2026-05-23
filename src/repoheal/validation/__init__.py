"""Validation pipeline.

A patch is never accepted unless every validator in its lane passes.

* :class:`ValidationPipeline` — ordered list of validators with
  short-circuit on FAIL and aggregation of WARNs.

Phase 1 validator:

* :class:`SyntaxValidator` (Python) — uses :mod:`ast`.

Phase 2 validators (sandbox-backed):

* :class:`RuffLintValidator`
* :class:`MypyTypeValidator`
* :class:`PytestValidator`

Each validator can be invoked standalone or composed in a pipeline.
The default pipeline order goes cheap-to-expensive: syntax → lint →
types → tests.
"""

from .checks import (
    MypyTypeValidator,
    PytestValidator,
    RuffLintValidator,
    SyntaxValidator,
)
from .pipeline import ValidationPipeline


def default_pipeline(*, sandbox=None) -> ValidationPipeline:
    """Construct the canonical validation pipeline.

    Order: syntax → lint → types → tests. Failing the cheaper checks
    short-circuits the pipeline before we pay for the expensive ones.
    """
    return ValidationPipeline(
        [
            SyntaxValidator(),
            RuffLintValidator(sandbox=sandbox),
            MypyTypeValidator(sandbox=sandbox),
            PytestValidator(sandbox=sandbox),
        ]
    )


__all__ = [
    "MypyTypeValidator",
    "PytestValidator",
    "RuffLintValidator",
    "SyntaxValidator",
    "ValidationPipeline",
    "default_pipeline",
]
