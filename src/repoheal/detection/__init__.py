"""Defect-detection subsystem.

A rule is a class implementing :class:`DetectionRule`. Rules are
registered with a :class:`RuleRegistry`; the registry can run every rule
or a named subset.

Phase 1 ships one rule: ``circular_imports``. Adding a rule is a
one-class affair (see :mod:`repoheal.detection.rules.python_rules` for
the canonical example).
"""

from .registry import RuleRegistry, default_registry
from .rules.python_rules import CircularImportRule

__all__ = ["CircularImportRule", "RuleRegistry", "default_registry"]
