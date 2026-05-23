"""Cross-subsystem Protocols.

Every place where one subsystem talks to another goes through one of
these. This file is the architectural contract: changing it requires
revisiting the architecture document and (probably) an ADR.

We use ``typing.Protocol`` rather than abstract base classes because:

* it permits structural typing (testing fakes need not subclass
  anything);
* it composes well with mypy's strict mode;
* it makes the dependency direction explicit — consumers depend on
  the Protocol, not on any concrete implementation.

There is exactly one Protocol per architectural seam. If you find
yourself adding a second Protocol with overlapping responsibility,
that is a smell: collapse them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .models import (
        Ecosystem,
        FileRef,
        Finding,
        ImportEdge,
        Issue,
        Language,
        ParsedFile,
        Patch,
        Repository,
        Symbol,
        ValidationReport,
        ValidationResult,
    )


# =============================================================================
# Ingestion
# =============================================================================


@runtime_checkable
class RepositoryCloner(Protocol):
    """Bring a repository onto local disk.

    Implementations: ``GitCloner`` (production), ``LocalPathCloner``
    (no-op for already-on-disk repos), ``InMemoryCloner`` (tests)."""

    def clone(self, source: str, *, branch: str | None = None) -> Path:
        """Place the repository at a fresh path on disk and return it."""


@runtime_checkable
class EcosystemDetector(Protocol):
    """Inspect a repo root and classify its ecosystem (pure function)."""

    def detect(self, root: Path) -> Ecosystem: ...


@runtime_checkable
class RepositoryWalker(Protocol):
    """Iterate over the in-scope source files of a repository.

    Implementations decide what "in scope" means: gitignore-aware,
    size-bounded, binary-skipping. The Protocol itself only promises an
    iterable of ``FileRef``."""

    def walk(self, root: Path) -> Iterable[FileRef]: ...


# =============================================================================
# Code intelligence
# =============================================================================


@runtime_checkable
class SourceParser(Protocol):
    """Parse a single file into a ``ParsedFile``.

    Implementations dispatch on language internally; callers do not need
    to pick the right parser."""

    def parse(self, file: FileRef, source: bytes) -> ParsedFile: ...


@runtime_checkable
class SymbolExtractor(Protocol):
    """Extract structured symbols from a parsed file. Pure function."""

    def extract(self, parsed: ParsedFile, source: bytes) -> Sequence[Symbol]: ...

    @property
    def language(self) -> Language: ...


@runtime_checkable
class ImportResolver(Protocol):
    """Resolve a module name to a concrete file in this repo, if possible.

    Returning ``None`` means "we couldn't resolve it" (e.g. third-party
    import); that's a normal outcome, not an error."""

    def resolve(
        self,
        edge: ImportEdge,
        repo: Repository,
    ) -> Path | None: ...


# =============================================================================
# Knowledge graph
# =============================================================================


@runtime_checkable
class GraphBackend(Protocol):
    """Storage + algorithm layer for the repository knowledge graph.

    The Protocol is intentionally small. Higher-level analyses
    (impact analysis, root-cause tracing) live in
    ``repoheal.graph.queries`` and are implemented in terms of these
    primitives, so they work uniformly across NetworkX, Neo4j, or any
    future backend.
    """

    # --- mutation -----------------------------------------------------
    def add_node(self, node_id: str, /, **attrs: Any) -> None: ...
    def add_edge(self, src: str, dst: str, /, *, kind: str, **attrs: Any) -> None: ...
    def has_node(self, node_id: str) -> bool: ...

    # --- read ---------------------------------------------------------
    def neighbors(
        self,
        node_id: str,
        *,
        kind: str | None = None,
        direction: str = "out",
    ) -> Iterable[str]: ...

    def node_attrs(self, node_id: str) -> dict[str, Any]: ...

    def all_nodes(self) -> Iterable[str]: ...

    def find_cycles(self, *, kind: str | None = None) -> list[list[str]]: ...

    # --- bulk ---------------------------------------------------------
    def node_count(self) -> int: ...
    def edge_count(self) -> int: ...
    def to_dict(self) -> dict[str, Any]: ...


# =============================================================================
# Detection
# =============================================================================


@runtime_checkable
class DetectionRule(Protocol):
    """A single defect-detection rule."""

    @property
    def rule_id(self) -> str: ...

    @property
    def title(self) -> str: ...

    def scan(self, repo: Repository, graph: GraphBackend) -> Sequence[Finding]: ...


# =============================================================================
# Patching
# =============================================================================


@runtime_checkable
class PatchApplier(Protocol):
    """Apply a Patch to a repository's working tree, with rollback on failure."""

    def apply(self, patch: Patch, repo: Repository) -> None: ...
    def rollback(self, repo: Repository) -> None: ...


# =============================================================================
# Validation
# =============================================================================


@runtime_checkable
class Validator(Protocol):
    """A single check that can pass/warn/fail/error a candidate patch.

    Validators are invoked **after** the patch has been applied to a
    snapshot of the repository (the pipeline owns the snapshot/rollback
    machinery)."""

    @property
    def name(self) -> str: ...

    def validate(self, repo: Repository, patch: Patch) -> ValidationResult: ...


@runtime_checkable
class ValidationPipeline(Protocol):
    """An ordered list of validators that produces a ``ValidationReport``."""

    def run(self, repo: Repository, patch: Patch) -> ValidationReport: ...


# =============================================================================
# Sandbox
# =============================================================================


@runtime_checkable
class SandboxRunner(Protocol):
    """Run a command inside an isolated environment with a timeout.

    Implementations: ``SubprocessRunner`` (development), ``DockerSandbox``
    (production, planned)."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> SandboxResult: ...


class SandboxResult:
    """Light-weight result type for sandbox executions.

    Defined here (not in models.py) because it is intentionally not a
    Pydantic model: it carries raw bytes and is created on a hot path."""

    __slots__ = ("returncode", "stdout", "stderr", "duration_seconds", "timed_out")

    def __init__(
        self,
        *,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
        duration_seconds: float,
        timed_out: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.duration_seconds = duration_seconds
        self.timed_out = timed_out

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timed_out


# =============================================================================
# Issues / agents (interface-defined; no implementation in Phase 1)
# =============================================================================


@runtime_checkable
class IssueSource(Protocol):
    """Fetch an issue from an external tracker."""

    def fetch(self, repo: str, number: int | str) -> Issue: ...


@runtime_checkable
class Agent(Protocol):
    """A single specialized agent in the multi-agent system.

    Phase 6 work. Defined now so consumers (the planner, observability,
    persistence) compile against a stable shape."""

    @property
    def name(self) -> str: ...

    async def run(self, state: dict[str, Any]) -> dict[str, Any]: ...
