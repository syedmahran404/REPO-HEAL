"""Runtime intelligence: parse and correlate runtime artefacts.

Phase 2 ships:

* :class:`TracebackParser` — turn a Python traceback (raw or embedded
  in log output) into structured :class:`StackFrame` records and an
  exception type/message.
* :class:`TracebackCorrelator` — map each parsed frame to the closest
  containing :class:`~repoheal.core.protocols.GraphBackend` node, with
  a confidence score and the qualified name of the symbol the frame
  fell inside.

These primitives feed three downstream consumers:

* the GitHub issue solver (issue body → candidate files / functions);
* the agent state for the :class:`RootCauseAgent` (anchor at the
  innermost frame's symbol);
* future runtime-tracing hooks (capture exceptions in test runs and
  correlate them automatically).
"""

from .correlation import (
    CorrelatedFrame,
    CorrelatedTraceback,
    TracebackCorrelator,
)
from .traceback_parser import (
    ParsedTraceback,
    StackFrame,
    TracebackParser,
)

__all__ = [
    "CorrelatedFrame",
    "CorrelatedTraceback",
    "ParsedTraceback",
    "StackFrame",
    "TracebackCorrelator",
    "TracebackParser",
]
