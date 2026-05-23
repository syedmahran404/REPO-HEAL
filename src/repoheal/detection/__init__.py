"""Defect-detection subsystem.

A rule is a class implementing :class:`DetectionRule`. Rules are
registered with a :class:`RuleRegistry`; the registry can run every rule
or a named subset.

Phase 1: ``circular_imports``.

Phase 2 adds: ``unused_imports``, ``mutable_default_args``,
``broad_except``, ``dead_code``, ``long_method``, ``god_class``,
``hardcoded_secret``. All rules emit ``confidence`` and a ``severity``.
"""

from .registry import RuleRegistry, default_registry
from .rules import (
    BroadExceptRule,
    CircularImportRule,
    DeadCodeRule,
    GodClassRule,
    HardcodedSecretRule,
    LongMethodRule,
    MutableDefaultArgsRule,
    UnusedImportRule,
)

__all__ = [
    "BroadExceptRule",
    "CircularImportRule",
    "DeadCodeRule",
    "GodClassRule",
    "HardcodedSecretRule",
    "LongMethodRule",
    "MutableDefaultArgsRule",
    "RuleRegistry",
    "UnusedImportRule",
    "default_registry",
]
