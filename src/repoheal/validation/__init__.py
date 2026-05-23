"""Validation pipeline.

A patch is never accepted unless every validator in its lane passes.

* :class:`ValidationPipeline` — ordered list of validators, with
  short-circuit on FAIL and aggregation of WARNs.
* :class:`SyntaxValidator` (Python) — uses :mod:`ast`. Real, tested.

Other validators (lint, type-check, unit tests, build, security scan)
are interface-defined; each will be a thin wrapper around its
canonical tool, run inside the sandbox.
"""

from .checks.syntax import SyntaxValidator
from .pipeline import ValidationPipeline

__all__ = ["SyntaxValidator", "ValidationPipeline"]
