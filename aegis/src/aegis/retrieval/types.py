"""Pydantic/stdlib-only schema types crossing the retrieval boundary.

Moved out of a host backend's API-schema module (the locked API-contract layer) so
`aegis.retrieval` has no dependency on any host application's schema layer. A host
that wants API-schema identity (so its own request/response models and this
package's never diverge) should re-export these directly rather than redefining
them — see the backend's strangler shim over its schema module.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

__all__ = ["FusionMethod", "GraphEdge", "GraphNode", "RetrievalOrigin"]


class RetrievalOrigin(StrEnum):
    """Where a retrieved candidate came from, for honest provenance."""

    VECTOR = "vector"
    GRAPH = "graph"
    BM25 = "bm25"
    CACHE = "cache"


class FusionMethod(StrEnum):
    """How multiple ranked recall lists were combined into one."""

    NONE = "none"  # single list, no fusion applied
    RRF = "rrf"  # reciprocal rank fusion
    MIX = "mix"  # delegated to a backend's internal graph+vector blend


class GraphNode(BaseModel):
    """A node in the knowledge-graph visualisation."""

    id: str
    label: str
    kind: str = Field(description="Entity kind/type for colouring the viz.")


class GraphEdge(BaseModel):
    """A directed, labelled edge between two graph nodes."""

    source: str
    target: str
    relation: str
