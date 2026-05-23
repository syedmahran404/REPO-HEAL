"""Repository ingestion: getting a repo onto disk and into our domain model.

Public surface:

* :class:`IngestionService` — orchestrates clone (or path adoption),
  ecosystem detection, and walking. Returns a fully populated
  :class:`~repoheal.core.models.Repository`.

The submodules are wired together via the Protocols in
:mod:`repoheal.core.protocols`, so swapping the cloner (e.g. for a fake
in tests) is a single constructor argument away.
"""

from .cloner import GitCloner, LocalPathCloner
from .detector import EcosystemDetector
from .service import IngestionService
from .walker import RepositoryWalker

__all__ = [
    "EcosystemDetector",
    "GitCloner",
    "IngestionService",
    "LocalPathCloner",
    "RepositoryWalker",
]
