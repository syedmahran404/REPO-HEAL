"""Rule registry.

The registry runs rules; rules don't know about each other. This makes
parallel execution trivial in a future PR (the registry is the unit of
parallelism, not the rule).
"""

from __future__ import annotations

from collections.abc import Iterable

from ..core.models import Finding, Repository
from ..core.protocols import DetectionRule, GraphBackend
from ..exceptions import DetectionError
from ..logging import get_logger

_log = get_logger(__name__)


class RuleRegistry:
    """Holds and runs detection rules."""

    def __init__(self, rules: Iterable[DetectionRule] | None = None) -> None:
        self._by_id: dict[str, DetectionRule] = {}
        if rules:
            for r in rules:
                self.register(r)

    def register(self, rule: DetectionRule) -> None:
        if rule.rule_id in self._by_id:
            raise ValueError(f"rule {rule.rule_id!r} already registered")
        self._by_id[rule.rule_id] = rule

    def get(self, rule_id: str) -> DetectionRule | None:
        return self._by_id.get(rule_id)

    def all(self) -> list[DetectionRule]:
        return list(self._by_id.values())

    def ids(self) -> list[str]:
        return list(self._by_id)

    # ------------------------------------------------------------------

    def scan(
        self,
        repo: Repository,
        graph: GraphBackend,
        *,
        rule_ids: Iterable[str] | None = None,
    ) -> list[Finding]:
        """Run rules and return their findings.

        If ``rule_ids`` is None, every registered rule runs. Unknown
        rule ids raise :class:`DetectionError` — silent skipping would
        be a footgun.
        """
        rules: list[DetectionRule]
        if rule_ids is None:
            rules = list(self._by_id.values())
        else:
            rules = []
            for rid in rule_ids:
                r = self._by_id.get(rid)
                if r is None:
                    raise DetectionError(f"unknown rule: {rid!r}")
                rules.append(r)

        findings: list[Finding] = []
        for rule in rules:
            try:
                rule_findings = list(rule.scan(repo, graph))
            except Exception as exc:
                _log.warning(
                    "detection.rule_failed",
                    rule_id=rule.rule_id,
                    error=str(exc),
                )
                continue
            _log.info(
                "detection.rule_done",
                rule_id=rule.rule_id,
                count=len(rule_findings),
            )
            findings.extend(rule_findings)
        return findings


# --- default registry ------------------------------------------------------


def default_registry() -> RuleRegistry:
    """Construct the canonical rule registry.

    As we add rules in subsequent PRs, register them here.
    """
    # Local import to avoid circular import with the rules module.
    from .rules.python_rules import CircularImportRule

    return RuleRegistry([CircularImportRule()])
