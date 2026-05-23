"""Core domain types and Protocols.

This package is the architectural spine of REPO-HEAL. Every other
subsystem depends on it; it depends on no other subsystem.

* :mod:`repoheal.core.models` — Pydantic value objects (``Repository``,
  ``FileRef``, ``Symbol``, ``Finding``, ``Patch``, etc).
* :mod:`repoheal.core.protocols` — every cross-subsystem seam expressed
  as a ``typing.Protocol``.
"""
