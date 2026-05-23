"""Domain value objects.

These types are the *lingua franca* between subsystems. They are
deliberately:

* immutable-ish (frozen pydantic models where reasonable);
* serialisable (cross the HTTP boundary unchanged);
* devoid of behaviour beyond simple derived properties (services hold
  behaviour, not data classes).

If you find yourself adding a method that does I/O, parsing, graph
mutation, or LLM calls to one of these — you are in the wrong file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# Repository
# =============================================================================


class Language(str, Enum):
    """Supported source languages.

    Phase 1 has full support for ``PYTHON``; the rest are detected and
    registered, with extractors implemented incrementally.
    """

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JAVA = "java"
    GO = "go"
    RUST = "rust"
    C = "c"
    CPP = "cpp"
    CSHARP = "csharp"
    PHP = "php"
    KOTLIN = "kotlin"
    SWIFT = "swift"
    RUBY = "ruby"
    UNKNOWN = "unknown"


class BuildSystem(str, Enum):
    POETRY = "poetry"
    PIP = "pip"
    UV = "uv"
    NPM = "npm"
    PNPM = "pnpm"
    YARN = "yarn"
    MAVEN = "maven"
    GRADLE = "gradle"
    CARGO = "cargo"
    GO_MODULES = "go-modules"
    CMAKE = "cmake"
    DOTNET = "dotnet"
    COMPOSER = "composer"
    BUNDLER = "bundler"
    SWIFT_PM = "swift-pm"
    UNKNOWN = "unknown"


class Ecosystem(BaseModel):
    """Snapshot of what a repository *is*: its languages, build tools,
    test frameworks, and any frameworks the detector recognises."""

    model_config = ConfigDict(frozen=True)

    languages: tuple[Language, ...] = ()
    build_systems: tuple[BuildSystem, ...] = ()
    frameworks: tuple[str, ...] = ()
    test_frameworks: tuple[str, ...] = ()
    package_managers: tuple[str, ...] = ()
    has_dockerfile: bool = False
    has_ci: bool = False

    @property
    def primary_language(self) -> Language:
        """First language in detection order, or ``UNKNOWN`` if empty."""
        return self.languages[0] if self.languages else Language.UNKNOWN


class FileRef(BaseModel):
    """A handle to a single source file inside a repository.

    ``path`` is **always relative** to the repository root. Absolute paths
    are an implementation detail of the walker and do not cross subsystem
    boundaries.
    """

    model_config = ConfigDict(frozen=True)

    path: Path
    language: Language = Language.UNKNOWN
    size_bytes: int
    is_binary: bool = False
    sha256: str | None = None  # populated when content is read

    @field_validator("path")
    @classmethod
    def _path_must_be_relative(cls, v: Path) -> Path:
        if v.is_absolute():
            raise ValueError(f"FileRef.path must be relative, got {v!r}")
        return v


class Repository(BaseModel):
    """An ingested repository: its identity, its on-disk root, the files
    we found in it, and our ecosystem fingerprint.

    The ``root`` is the on-disk path the rest of the system uses to
    actually read content. ``files`` is the canonical, ordered list of
    files we consider in scope (binaries, large files, and gitignored
    paths are excluded by the walker)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: UUID = Field(default_factory=uuid4)
    name: str
    root: Path
    origin: str | None = None  # URL if cloned, else None
    branch: str | None = None
    commit: str | None = None
    ecosystem: Ecosystem = Field(default_factory=Ecosystem)
    files: list[FileRef] = Field(default_factory=list)
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def file_count(self) -> int:
        return len(self.files)


# =============================================================================
# Code intelligence
# =============================================================================


class SymbolKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"
    PARAMETER = "parameter"
    DECORATOR = "decorator"


class SourceRange(BaseModel):
    """Inclusive byte range and (line, col) tuples within a file."""

    model_config = ConfigDict(frozen=True)

    start_byte: int
    end_byte: int
    start_line: int
    start_col: int
    end_line: int
    end_col: int


class Symbol(BaseModel):
    """A named entity extracted from a parsed source file."""

    model_config = ConfigDict(frozen=True)

    name: str
    qualified_name: str  # e.g. "package.module.ClassName.method_name"
    kind: SymbolKind
    file: Path  # relative to repo root
    range: SourceRange
    parent: str | None = None  # qualified_name of enclosing symbol
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImportEdge(BaseModel):
    """An import statement we extracted: who imports what, from where."""

    model_config = ConfigDict(frozen=True)

    source_file: Path  # relative to repo root
    target_module: str  # the imported module path as written
    resolved_file: Path | None = None  # set by ImportResolver if found
    is_relative: bool = False
    alias: str | None = None


class UnresolvedCall(BaseModel):
    """A textual call site whose callee has not yet been resolved.

    Resolution happens in the graph builder, which has the global
    symbol index. Keeping resolution as a separate phase keeps the
    parser pure-lexical and makes the resolver swappable (heuristic
    today, type-aware tomorrow).
    """

    model_config = ConfigDict(frozen=True)

    file: Path  # relative
    caller_qname: str  # qualified name of the enclosing function/method/module
    callee_text: str  # what the source said: ``foo``, ``obj.bar``, ``pkg.mod.baz``
    range: SourceRange


class UnresolvedInheritance(BaseModel):
    """A `class Foo(Bar)` superclass whose target has not been resolved."""

    model_config = ConfigDict(frozen=True)

    file: Path
    child_qname: str  # qualified name of the subclass
    parent_text: str  # textual base, e.g. ``Bar`` or ``pkg.Bar``
    range: SourceRange


class UnresolvedReference(BaseModel):
    """A non-call reference to a symbol (e.g. a decorator target).

    Decorators are the highest-signal references for "is this still
    used?" analysis: framework-registered functions look dead by
    call graph alone but are alive via @register-style references.
    """

    model_config = ConfigDict(frozen=True)

    file: Path
    referrer_qname: str
    target_text: str
    range: SourceRange


class ParsedFile(BaseModel):
    """The structured outcome of parsing a single file."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    file: FileRef
    language: Language
    symbols: list[Symbol] = Field(default_factory=list)
    imports: list[ImportEdge] = Field(default_factory=list)
    calls: list[UnresolvedCall] = Field(default_factory=list)
    inherits: list[UnresolvedInheritance] = Field(default_factory=list)
    references: list[UnresolvedReference] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)


# =============================================================================
# Findings (output of detection)
# =============================================================================


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Finding(BaseModel):
    """A defect or issue surfaced by a detection rule."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    rule_id: str
    title: str
    description: str
    severity: Severity = Severity.MEDIUM
    file: Path | None = None
    range: SourceRange | None = None
    related_files: tuple[Path, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Retrieval (Phase 2)
# =============================================================================


class Chunk(BaseModel):
    """A retrievable unit of source content.

    The retrieval system slices a repository into chunks at symbol
    boundaries (via :class:`SymbolAwareChunker`) and indexes them with
    BM25 + dense vectors. Each chunk carries enough context to be
    self-contained when packed into an LLM's prompt: source text,
    file path, line range, and a back-reference to its symbol's
    qualified name (so the graph expansion stage can find structural
    neighbors).
    """

    model_config = ConfigDict(frozen=True)

    id: str  # deterministic: "<file_path>::<symbol_qname>" or "<file_path>::lines:<a>-<b>"
    file_path: Path
    text: str
    start_line: int
    end_line: int
    symbol_qname: str | None = None
    language: Language = Language.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Patches & validation
# =============================================================================


class FileEdit(BaseModel):
    """A proposed change to a single file's contents.

    Always full-file replacement at the model level; diff representation
    is generated by the patching layer. Two reasons:
      1. Eliminates a class of "applied at the wrong line" bugs.
      2. The validator only ever sees a final state, never a delta.
    """

    model_config = ConfigDict(frozen=True)

    file: Path  # relative
    new_content: str
    is_new_file: bool = False
    is_deletion: bool = False


class Patch(BaseModel):
    """A coherent set of edits proposed as a single fix.

    A patch is the *unit* of validation. The validator either accepts
    the whole patch or rejects it; we never partially apply.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    title: str
    description: str
    edits: tuple[FileEdit, ...]
    related_finding_ids: tuple[UUID, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class Verdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    ERROR = "error"  # the validator itself crashed


class ValidationResult(BaseModel):
    """One validator's verdict on a patch."""

    model_config = ConfigDict(frozen=True)

    validator: str
    verdict: Verdict
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    """The aggregated output of a ValidationPipeline."""

    model_config = ConfigDict(frozen=True)

    overall: Verdict
    results: tuple[ValidationResult, ...]

    @property
    def passed(self) -> bool:
        return self.overall == Verdict.PASS


# =============================================================================
# Issues & jobs
# =============================================================================


class Issue(BaseModel):
    """A repository issue (e.g. GitHub issue) for the autonomous solver."""

    model_config = ConfigDict(frozen=True)

    source: str  # "github", "gitlab", "local", ...
    number: int | str
    title: str
    body: str
    labels: tuple[str, ...] = ()
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMEOUT = "timeout"


class JobError(BaseModel):
    model_config = ConfigDict(frozen=True)

    where: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobResult(BaseModel):
    """The terminal value of any long-running operation in REPO-HEAL.

    There is no silent failure: every job ends with a status. ``PARTIAL``
    is first-class so an ingestion that successfully parsed 9,998 of 10,000
    files can still be useful downstream, while reporting the two it
    failed on."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    job_id: UUID = Field(default_factory=uuid4)
    status: JobStatus
    started_at: datetime
    finished_at: datetime
    summary: str = ""
    errors: tuple[JobError, ...] = ()
    payload: Any = None


__all__ = [
    "BuildSystem",
    "Chunk",
    "Ecosystem",
    "FileEdit",
    "FileRef",
    "Finding",
    "ImportEdge",
    "Issue",
    "JobError",
    "JobResult",
    "JobStatus",
    "Language",
    "ParsedFile",
    "Patch",
    "Repository",
    "Severity",
    "SourceRange",
    "Symbol",
    "SymbolKind",
    "UnresolvedCall",
    "UnresolvedInheritance",
    "UnresolvedReference",
    "ValidationReport",
    "ValidationResult",
    "Verdict",
]
