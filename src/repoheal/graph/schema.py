"""Graph node and edge schema.

The graph is heterogeneous: nodes have a ``kind`` (``File``, ``Module``,
``Class``, ``Function``, ...) and edges have a ``kind`` (``CONTAINS``,
``IMPORTS``, ``CALLS``, ...).

Node IDs are deterministic strings derived from the entity they
represent. This means:

* the same logical entity always gets the same id across runs
  (incremental indexing-friendly);
* equality is string equality (cheap);
* ids are debuggable in JSON dumps.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path


class NodeKind(str, Enum):
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"
    SERVICE = "service"
    ENDPOINT = "endpoint"


class EdgeKind(str, Enum):
    """Why two nodes are connected. Edge kind drives every traversal."""

    CONTAINS = "contains"  # File → Module → Class → Method
    IMPORTS = "imports"    # Module → Module
    CALLS = "calls"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    RAISES = "raises"
    REFERENCES = "references"
    READS = "reads"
    WRITES = "writes"


# --- ID helpers ------------------------------------------------------------
#
# Node IDs are deliberately string-typed (not URIs, not GUIDs) for
# debuggability. Two rules:
#   1. Always include the kind as a prefix so two entities of different
#      kinds with the same human name never collide.
#   2. Always normalise paths to POSIX form so Windows ↔ Linux runs
#      produce identical ids.


def file_node_id(rel_path: Path | str) -> str:
    return f"file::{Path(rel_path).as_posix()}"


def module_node_id(qualified_name: str) -> str:
    return f"module::{qualified_name}"


def symbol_node_id(qualified_name: str, *, kind: NodeKind) -> str:
    return f"{kind.value}::{qualified_name}"
