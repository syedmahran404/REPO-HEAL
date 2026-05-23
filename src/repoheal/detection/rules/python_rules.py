"""Python-specific detection rules.

Phase 1 implements ``CircularImportRule`` end-to-end. The rule walks the
``IMPORTS`` edges in the knowledge graph, finds strongly-connected
components of size > 1, and emits a :class:`Finding` per cycle with the
modules involved and (best-effort) the file paths.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ...core.models import Finding, Repository, Severity
from ...core.protocols import GraphBackend
from ...graph.schema import EdgeKind


class CircularImportRule:
    """Detect cycles in module-level import graphs.

    A circular import is rarely a bug *until* it is — the moment one
    side starts using a top-level symbol from the other, you get an
    ``ImportError`` at module load. We surface every cycle so you can
    decide which to break before that day comes.
    """

    @property
    def rule_id(self) -> str:
        return "circular_imports"

    @property
    def title(self) -> str:
        return "Circular import"

    # ------------------------------------------------------------------

    def scan(self, repo: Repository, graph: GraphBackend) -> Sequence[Finding]:
        cycles = graph.find_cycles(kind=EdgeKind.IMPORTS.value)
        findings: list[Finding] = []
        for cycle in cycles:
            internal_modules = [
                node_id for node_id in cycle if not _is_external(graph, node_id)
            ]
            if len(internal_modules) < 2:
                # SCC of 2+ but only one is internal: that's a self-edge
                # to an external lib, not a cycle in *our* code.
                continue

            modules = [_qname(graph, node_id) for node_id in internal_modules]
            files = tuple(
                _file_for_module(graph, node_id)
                for node_id in internal_modules
                if _file_for_module(graph, node_id) is not None
            )
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    title=f"Circular import among {len(modules)} modules",
                    description=(
                        "These modules form an import cycle:\n  - "
                        + "\n  - ".join(modules)
                        + "\n\nA cycle is fine while neither side touches the "
                        "other's top-level names. The first time it does, "
                        "Python raises ImportError at module load."
                    ),
                    severity=Severity.MEDIUM,
                    file=Path(files[0]) if files else None,
                    related_files=tuple(Path(f) for f in files),  # type: ignore[arg-type]
                    metadata={
                        "modules": modules,
                        "cycle_size": len(modules),
                    },
                )
            )
        return findings


# --- helpers --------------------------------------------------------------


def _qname(graph: GraphBackend, node_id: str) -> str:
    attrs = graph.node_attrs(node_id)
    qn = attrs.get("qualified_name")
    if isinstance(qn, str):
        return qn
    return node_id


def _file_for_module(graph: GraphBackend, node_id: str) -> str | None:
    attrs = graph.node_attrs(node_id)
    f = attrs.get("file")
    return f if isinstance(f, str) else None


def _is_external(graph: GraphBackend, node_id: str) -> bool:
    return bool(graph.node_attrs(node_id).get("external", False))
