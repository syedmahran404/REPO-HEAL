"""Multi-agent system — INTERFACE-DEFINED ONLY in Phase 1.

The orchestration model is documented in
:doc:`docs/ARCHITECTURE.md` §4.5. Implementation is gated on the
choice of state-machine vs. LangGraph (ADR-0004, forthcoming).

Agents and their contracts:

* ``RepositoryArchitectAgent`` — produces a high-level structural map.
* ``DependencyAnalysisAgent`` — surfaces fragile / risky dependencies.
* ``BugDetectionAgent`` — orchestrates rule-based + LLM-based detectors.
* ``RootCauseAgent`` — walks the graph backward from a failure.
* ``SecurityAnalysisAgent`` — surfaces secrets, weak crypto, etc.
* ``RefactoringAgent`` — proposes structural improvements.
* ``PatchGenerationAgent`` — proposes a Patch given a Finding + context.
* ``RegressionPreventionAgent`` — proposes additional tests around a fix.
* ``TestGenerationAgent`` — generates tests for uncovered code.
* ``ValidationAgent`` — wraps the ValidationPipeline as an agent step.
* ``PerformanceOptimizationAgent`` — surfaces hot paths from telemetry.
* ``DocumentationAgent`` — generates / updates docstrings & ADRs.
* ``CICDAgent`` — proposes CI workflow changes.
* ``AutonomousPlanningAgent`` — top-level planner.

Each agent ultimately produces a structured result; the planner
composes them. None of this is implemented in Phase 1.
"""

from .base import AgentResult, AgentStatus
from .protocols import Agent, Plan, PlannerAgent, ToolCall

__all__ = [
    "Agent",
    "AgentResult",
    "AgentStatus",
    "Plan",
    "PlannerAgent",
    "ToolCall",
]
