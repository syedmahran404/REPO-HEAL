"""Exception hierarchy for REPO-HEAL.

Every error a subsystem can raise is rooted at ``RepoHealError`` so callers
can do a single ``except RepoHealError`` at the top of an agent loop or
HTTP handler. Subclasses are organized by subsystem so observability tools
can group failures.
"""

from __future__ import annotations


class RepoHealError(Exception):
    """Base class for every error raised by REPO-HEAL."""


# --- Ingestion ---------------------------------------------------------------


class IngestionError(RepoHealError):
    """Something went wrong cloning, walking, or detecting a repository."""


class CloneError(IngestionError):
    """Git clone failed (timeout, auth, network, bad URL)."""


class UnsupportedRepositoryError(IngestionError):
    """The repository structure could not be understood at all."""


# --- Intelligence ------------------------------------------------------------


class IntelligenceError(RepoHealError):
    """Parsing or symbol extraction failure."""


class UnsupportedLanguageError(IntelligenceError):
    """We have no parser/extractor registered for this language."""


class ParseError(IntelligenceError):
    """Tree-sitter (or fallback) failed to parse a file."""


# --- Graph -------------------------------------------------------------------


class GraphError(RepoHealError):
    """Generic graph backend failure."""


class NodeNotFoundError(GraphError):
    """A node was referenced that does not exist in the graph."""


# --- Detection / Patching / Validation --------------------------------------


class DetectionError(RepoHealError):
    """A detection rule failed during scanning."""


class PatchError(RepoHealError):
    """Patch generation, application, or rollback failed."""


class ValidationError(RepoHealError):
    """A validator itself crashed (note: a validator returning FAIL is normal,
    not an error)."""


# --- Sandbox -----------------------------------------------------------------


class SandboxError(RepoHealError):
    """Sandbox setup or execution failed."""


class SandboxTimeoutError(SandboxError):
    """A sandboxed command exceeded its wallclock budget."""
