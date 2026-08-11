"""Backend shim: retrieval data models now live in ``aegis.retrieval.models``.

Re-exported by identity (not redefined) so a `RetrievalResult`/`Candidate`/... built
by `aegis.retrieval` and one built here are the exact same class — no parallel schema
to drift.
"""

from __future__ import annotations

from aegis.retrieval.models import (
    CacheProvenance,
    Candidate,
    Chunk,
    GraphDelta,
    IngestReport,
    Provenance,
    Recall,
    RetrievalResult,
    Source,
)

__all__ = [
    "CacheProvenance",
    "Candidate",
    "Chunk",
    "GraphDelta",
    "IngestReport",
    "Provenance",
    "Recall",
    "RetrievalResult",
    "Source",
]
