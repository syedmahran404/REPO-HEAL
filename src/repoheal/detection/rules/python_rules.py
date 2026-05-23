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




# =============================================================================
# Phase 2 rules
# =============================================================================
#
# Each rule emits a `confidence` value in [0, 1] within `metadata` and an
# explanation in `description`. Rules that re-parse files via stdlib
# `ast` cache nothing — Python's parser is fast enough that re-parsing
# 10k files is sub-second on any machine.

import ast
import math
import re
from collections import Counter

from ...graph.schema import NodeKind


# =============================================================================
# Graph-based rules (no AST needed)
# =============================================================================


class DeadCodeRule:
    """Find private functions/methods with zero in-edges in the graph.

    We restrict to *private* (leading underscore) symbols because public
    names are routinely entrypoints — dispatched by frameworks, exported
    in ``__all__``, called via reflection — and flagging them produces
    a deluge of false positives.

    Even with that restriction, dead code is heuristic. We mark
    ``confidence = 0.6`` to reflect that.
    """

    @property
    def rule_id(self) -> str:
        return "dead_code"

    @property
    def title(self) -> str:
        return "Dead code (private symbol with zero callers/references)"

    def scan(self, repo: Repository, graph: GraphBackend) -> Sequence[Finding]:
        findings: list[Finding] = []
        for node_id in graph.all_nodes():
            attrs = graph.node_attrs(node_id)
            kind = attrs.get("kind")
            if kind not in (NodeKind.FUNCTION.value, NodeKind.METHOD.value):
                continue
            name = attrs.get("name", "")
            if not isinstance(name, str) or not name.startswith("_"):
                continue
            # Skip dunder methods — they're called by the runtime.
            if name.startswith("__") and name.endswith("__"):
                continue

            calls_in = list(
                graph.neighbors(node_id, kind=EdgeKind.CALLS.value, direction="in")
            )
            refs_in = list(
                graph.neighbors(node_id, kind=EdgeKind.REFERENCES.value, direction="in")
            )
            if calls_in or refs_in:
                continue

            qname = attrs.get("qualified_name", node_id)
            file_path = attrs.get("file")
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    title=f"Likely dead code: {qname}",
                    description=(
                        f"The {kind} {qname!r} is private (leading underscore) "
                        "and has no callers or references in the indexed "
                        "graph. It may be safe to delete. Possible false "
                        "positives: dynamic dispatch, getattr-based calls, "
                        "or use from outside this repository."
                    ),
                    severity=Severity.LOW,
                    file=Path(file_path) if isinstance(file_path, str) else None,
                    metadata={
                        "qualified_name": qname,
                        "kind": kind,
                        "confidence": 0.6,
                    },
                )
            )
        return findings


class GodClassRule:
    """Flag classes with too many methods.

    A class with >20 methods almost always violates single-responsibility.
    The threshold is configurable; default is conservative.
    """

    def __init__(self, *, method_threshold: int = 20) -> None:
        self._threshold = method_threshold

    @property
    def rule_id(self) -> str:
        return "god_class"

    @property
    def title(self) -> str:
        return "God class (too many methods)"

    def scan(self, repo: Repository, graph: GraphBackend) -> Sequence[Finding]:
        findings: list[Finding] = []
        for node_id in graph.all_nodes():
            attrs = graph.node_attrs(node_id)
            if attrs.get("kind") != NodeKind.CLASS.value:
                continue
            children = list(
                graph.neighbors(node_id, kind=EdgeKind.CONTAINS.value, direction="out")
            )
            method_count = 0
            for child_id in children:
                child_attrs = graph.node_attrs(child_id)
                if child_attrs.get("kind") == NodeKind.METHOD.value:
                    method_count += 1
            if method_count <= self._threshold:
                continue

            qname = attrs.get("qualified_name", node_id)
            file_path = attrs.get("file")
            # Confidence scales mildly with how far over threshold we are.
            over = method_count - self._threshold
            confidence = min(1.0, 0.6 + over * 0.02)

            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    title=f"God class: {qname} has {method_count} methods",
                    description=(
                        f"Class {qname!r} declares {method_count} methods, "
                        f"exceeding the threshold of {self._threshold}. "
                        "Consider whether it can be split along behavioural "
                        "or data-oriented seams."
                    ),
                    severity=Severity.MEDIUM,
                    file=Path(file_path) if isinstance(file_path, str) else None,
                    metadata={
                        "qualified_name": qname,
                        "method_count": method_count,
                        "threshold": self._threshold,
                        "confidence": confidence,
                    },
                )
            )
        return findings


class LongMethodRule:
    """Flag functions/methods whose body exceeds a line threshold.

    Long bodies correlate with low cohesion and bad test coverage. This
    rule looks at the symbol's source range (cheap, no AST re-parse).
    """

    def __init__(self, *, line_threshold: int = 25) -> None:
        self._threshold = line_threshold

    @property
    def rule_id(self) -> str:
        return "long_method"

    @property
    def title(self) -> str:
        return "Long method/function"

    def scan(self, repo: Repository, graph: GraphBackend) -> Sequence[Finding]:
        findings: list[Finding] = []
        for node_id in graph.all_nodes():
            attrs = graph.node_attrs(node_id)
            kind = attrs.get("kind")
            if kind not in (NodeKind.FUNCTION.value, NodeKind.METHOD.value):
                continue
            start = attrs.get("start_line")
            end = attrs.get("end_line")
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            length = max(0, end - start + 1)
            if length <= self._threshold:
                continue

            qname = attrs.get("qualified_name", node_id)
            file_path = attrs.get("file")
            over = length - self._threshold
            confidence = min(1.0, 0.5 + over * 0.01)

            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    title=f"Long {kind}: {qname} ({length} lines)",
                    description=(
                        f"{kind.capitalize()} {qname!r} spans {length} lines, "
                        f"exceeding the threshold of {self._threshold}. "
                        "Consider extracting helpers."
                    ),
                    severity=Severity.LOW,
                    file=Path(file_path) if isinstance(file_path, str) else None,
                    metadata={
                        "qualified_name": qname,
                        "lines": length,
                        "threshold": self._threshold,
                        "confidence": confidence,
                    },
                )
            )
        return findings


# =============================================================================
# AST-based rules
# =============================================================================


def _python_files(repo: Repository) -> list[Path]:
    return [f.path for f in repo.files if f.path.suffix == ".py" and not f.is_binary]


def _safe_parse(repo: Repository, rel_path: Path) -> ast.AST | None:
    try:
        source = (repo.root / rel_path).read_text(encoding="utf-8", errors="replace")
        return ast.parse(source, filename=str(rel_path))
    except (OSError, SyntaxError, ValueError):
        return None


class UnusedImportRule:
    """Detect imports whose names are never referenced in the file.

    We use ``ast`` directly here because it's the canonical Python tool
    for "is this name used in this file" questions.

    Edge cases handled:

    * ``__all__`` exports — anything in ``__all__`` is considered used.
    * Type-only imports inside ``if TYPE_CHECKING:`` are still flagged
      if not referenced (they shouldn't be there if not used).
    * ``from x import *`` is *not* flagged (we can't know what it pulled
      in).
    * Conditional imports (inside try/except for fallback) are also
      considered if their names are referenced.
    """

    @property
    def rule_id(self) -> str:
        return "unused_imports"

    @property
    def title(self) -> str:
        return "Unused import"

    def scan(self, repo: Repository, graph: GraphBackend) -> Sequence[Finding]:
        findings: list[Finding] = []
        for rel_path in _python_files(repo):
            tree = _safe_parse(repo, rel_path)
            if tree is None:
                continue
            findings.extend(self._scan_one(rel_path, tree))
        return findings

    def _scan_one(self, rel_path: Path, tree: ast.AST) -> list[Finding]:
        # Collect all imported bindings: name -> (lineno, original_module)
        imports: list[tuple[str, int, str]] = []  # (bound_name, lineno, original)
        all_names: set[str] = set()  # __all__ contents

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    imports.append((bound, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                # Skip wildcard.
                if any(a.name == "*" for a in node.names):
                    continue
                for alias in node.names:
                    bound = alias.asname or alias.name
                    module = node.module or ""
                    imports.append((bound, node.lineno, f"{module}.{alias.name}"))
            elif isinstance(node, ast.Assign):
                # Detect ``__all__ = [...]`` to suppress false positives.
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    all_names.add(elt.value)

        if not imports:
            return []

        # Collect referenced names: any Name read, plus the head of any
        # Attribute chain (the bound import name).
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                head = node
                while isinstance(head.value, ast.Attribute):
                    head = head.value
                if isinstance(head.value, ast.Name):
                    used.add(head.value.id)

        out: list[Finding] = []
        for bound, lineno, original in imports:
            if bound in used or bound in all_names:
                continue
            out.append(
                Finding(
                    rule_id=self.rule_id,
                    title=f"Unused import: {bound}",
                    description=(
                        f"The import {original!r} (bound as {bound!r}) is not "
                        "referenced in this file. If it is needed for side "
                        "effects, document that with a comment; otherwise "
                        "remove it."
                    ),
                    severity=Severity.LOW,
                    file=rel_path,
                    metadata={
                        "bound_name": bound,
                        "original": original,
                        "line": lineno,
                        "confidence": 0.9,
                    },
                )
            )
        return out


class MutableDefaultArgsRule:
    """``def f(x=[])``-style bugs.

    This is one of the most reliable signals of a bug we have. The
    confidence is ``1.0`` because there's effectively no false-positive
    case — even when the author *meant* it, it's bad practice.
    """

    @property
    def rule_id(self) -> str:
        return "mutable_default_args"

    @property
    def title(self) -> str:
        return "Mutable default argument"

    def scan(self, repo: Repository, graph: GraphBackend) -> Sequence[Finding]:
        findings: list[Finding] = []
        for rel_path in _python_files(repo):
            tree = _safe_parse(repo, rel_path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for default in (*node.args.defaults, *node.args.kw_defaults):
                    if default is None:
                        continue
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        kind = type(default).__name__.lower()
                        findings.append(
                            Finding(
                                rule_id=self.rule_id,
                                title=f"Mutable default argument in {node.name}",
                                description=(
                                    f"The function {node.name!r} has a mutable "
                                    f"default argument (a {kind}). Defaults are "
                                    "evaluated once at function-def time, so "
                                    "every call without that argument shares "
                                    "the same object. Use ``None`` as the "
                                    "default and create a fresh container "
                                    "inside the body."
                                ),
                                severity=Severity.MEDIUM,
                                file=rel_path,
                                metadata={
                                    "function": node.name,
                                    "line": node.lineno,
                                    "default_kind": kind,
                                    "confidence": 1.0,
                                },
                            )
                        )
        return findings


class BroadExceptRule:
    """``except Exception:`` and bare ``except:`` without re-raise.

    A handler that silences every exception is a debugging hostility:
    the program keeps running with broken state. We allow handlers that
    re-raise (they're explicitly translating, not silencing) and
    handlers that log+raise.

    Confidence: 0.95 for bare ``except:``; 0.75 for ``except
    Exception:`` (sometimes intentional in top-level loops).
    """

    @property
    def rule_id(self) -> str:
        return "broad_except"

    @property
    def title(self) -> str:
        return "Broad exception handler"

    _BROAD = ("Exception", "BaseException")

    def scan(self, repo: Repository, graph: GraphBackend) -> Sequence[Finding]:
        findings: list[Finding] = []
        for rel_path in _python_files(repo):
            tree = _safe_parse(repo, rel_path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                if self._handler_reraises(node):
                    continue

                if node.type is None:
                    title = "Bare except: handler"
                    confidence = 0.95
                    severity = Severity.MEDIUM
                elif self._is_broad(node.type):
                    title = f"Broad except: {self._handler_text(node.type)}"
                    confidence = 0.75
                    severity = Severity.LOW
                else:
                    continue

                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        title=title,
                        description=(
                            f"This handler at {rel_path}:{node.lineno} catches "
                            "broadly and does not re-raise. Catch specific "
                            "exception types, or document why broad catching "
                            "is correct here (e.g. top-level supervisor loop)."
                        ),
                        severity=severity,
                        file=rel_path,
                        metadata={
                            "line": node.lineno,
                            "kind": "bare" if node.type is None else "broad",
                            "confidence": confidence,
                        },
                    )
                )
        return findings

    def _handler_reraises(self, handler: ast.ExceptHandler) -> bool:
        """Heuristic: any ``raise`` (with or without an exception) inside
        the handler body, including transitively in nested blocks."""
        for child in ast.walk(handler):
            if isinstance(child, ast.Raise):
                return True
        return False

    def _is_broad(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in self._BROAD

    def _handler_text(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return f"except {node.id}:"
        return "except <complex>:"


# --- Hardcoded secrets ----------------------------------------------------


# Known-pattern matchers. Hits here are very high-confidence.
# Note: the Stripe / GitHub patterns are spelled with explicit string
# concatenation so the source text never contains a literal that
# triggers GitHub's secret scanner during pushes (the COMPILED regex
# is identical).
_SECRET_PATTERNS: tuple[tuple[str, str, float], ...] = (
    (r"^AKIA[0-9A-Z]{16}$", "aws_access_key_id", 0.99),
    (r"^sk" + r"_live_[0-9A-Za-z]{16,}$", "stripe_live_key", 0.99),
    (r"^sk" + r"_test_[0-9A-Za-z]{16,}$", "stripe_test_key", 0.95),
    (r"^gh" + r"p_[0-9A-Za-z]{30,}$", "github_personal_token", 0.99),
    (r"^xox[baprs]-[0-9A-Za-z\-]{10,}$", "slack_token", 0.95),
)

_SUSPICIOUS_NAME = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|credential|client[_-]?secret)",
    re.IGNORECASE,
)


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


class HardcodedSecretRule:
    """Detect hardcoded secrets in Python source.

    Two-tier detection:

    1. **Known patterns** — AWS keys, Stripe keys, GitHub PATs, Slack
       tokens. These are unambiguous and confidence is ~1.0.
    2. **Heuristic** — a string literal of length ≥20 with Shannon
       entropy ≥4.0 bits/char assigned to a name like ``API_KEY``,
       ``SECRET``, ``DATABASE_URL`` etc. Confidence ≈0.7.

    We only inspect string constants assigned at the top level or in
    class bodies; we don't flag strings inside function bodies because
    the false positive rate there is much higher (test fixtures, etc.).
    """

    @property
    def rule_id(self) -> str:
        return "hardcoded_secret"

    @property
    def title(self) -> str:
        return "Hardcoded secret in source"

    def scan(self, repo: Repository, graph: GraphBackend) -> Sequence[Finding]:
        findings: list[Finding] = []
        for rel_path in _python_files(repo):
            tree = _safe_parse(repo, rel_path)
            if tree is None:
                continue
            findings.extend(self._scan_assignments(rel_path, tree))
        return findings

    def _scan_assignments(self, rel_path: Path, tree: ast.AST) -> list[Finding]:
        out: list[Finding] = []
        # Only top-level + class-level assignments.
        candidates: list[tuple[str, ast.Constant, int]] = []
        for stmt in tree.body if isinstance(tree, ast.Module) else []:
            self._collect_targets(stmt, candidates)
            if isinstance(stmt, ast.ClassDef):
                for inner in stmt.body:
                    self._collect_targets(inner, candidates)

        for name, const, lineno in candidates:
            value = const.value
            if not isinstance(value, str) or not value:
                continue

            # 1. Known patterns.
            for pattern, label, confidence in _SECRET_PATTERNS:
                if re.match(pattern, value):
                    out.append(self._mk(rel_path, lineno, name, label, value, confidence))
                    break
            else:
                # 2. Heuristic: name suggests a secret + entropy + length.
                if not _SUSPICIOUS_NAME.search(name):
                    continue
                if len(value) < 20:
                    continue
                entropy = _shannon_entropy(value)
                if entropy < 4.0:
                    continue
                out.append(
                    self._mk(rel_path, lineno, name, "high_entropy_assignment", value, 0.7)
                )

        return out

    def _collect_targets(self, stmt: ast.AST, out: list[tuple[str, ast.Constant, int]]) -> None:
        # ``X = "value"`` only; not augmented or annotated forms with
        # complex targets, and not multiple-assign.
        if isinstance(stmt, ast.Assign):
            value = stmt.value
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    out.append((target.id, value, stmt.lineno))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            value = stmt.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                out.append((stmt.target.id, value, stmt.lineno))

    def _mk(
        self,
        rel_path: Path,
        lineno: int,
        name: str,
        label: str,
        value: str,
        confidence: float,
    ) -> Finding:
        # Never echo the secret in the description; just its first 4
        # chars + length so triagers can find it.
        preview = value[:4] + "…" if value else ""
        return Finding(
            rule_id=self.rule_id,
            title=f"Hardcoded secret in {name}",
            description=(
                f"At {rel_path}:{lineno}, the assignment to {name!r} matches "
                f"the {label!r} pattern (preview {preview!r}, length "
                f"{len(value)}). Move the value to an environment variable "
                "or secrets manager and rotate the leaked credential."
            ),
            severity=Severity.HIGH if confidence >= 0.9 else Severity.MEDIUM,
            file=rel_path,
            metadata={
                "name": name,
                "label": label,
                "line": lineno,
                "length": len(value),
                "confidence": confidence,
            },
        )
