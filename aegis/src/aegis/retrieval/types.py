"""Pydantic/stdlib-only schema types crossing the retrieval boundary.

Moved out of a host backend's API-schema module (the locked API-contract layer) so
`aegis.retrieval` has no dependency on any host application's schema layer. A host
that wants API-schema identity (so its own request/response models and this
package's never diverge) should re-export these directly rather than redefining
them — see the backend's strangler shim over its schema module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

__all__ = [
    "TENANT_METADATA_KEY",
    "FusionMethod",
    "GraphEdge",
    "GraphNode",
    "RetrievalOrigin",
    "RetrievalScope",
    "tenant_metadata_value",
]

#: The single metadata key under which a row's owning tenant is recorded — on a
#: :class:`~aegis.retrieval.models.Chunk`, on a vector-store payload, and in every
#: ``where`` filter that scopes a search. One name, defined once, so a writer and a
#: reader cannot disagree about where the tenant lives.
TENANT_METADATA_KEY = "tenant_id"


def tenant_metadata_value(tenant_id: int | None) -> str | None:
    """Return the canonical **metadata** value recording which tenant owns a row.

    Every tenant-scoped row (a vector-store payload, a knowledge-backend chunk) carries
    its owner under one canonical value produced here, so the write side and the read
    side cannot drift apart.

    It is a *string* token rather than the raw ``int`` for one concrete reason: Chroma
    rejects a ``$in`` list whose values are not all of the same type, and a tenant-scoped
    search must ask for ``[<this tenant>, <shared>]`` in one clause — where "shared" is
    stored as :data:`~aegis.retrieval.vector_store._NULL`, a *string* sentinel (Chroma
    silently drops a literal ``None`` metadata value, which would turn a null tenant into
    a wildcard). Encoding the tenant as ``"t<id>"`` keeps that list homogeneous.

    Args:
        tenant_id: The owning tenant, or ``None`` for a row in the **shared** corpus
            (knowledge that belongs to no single tenant, e.g. a bundled adapter corpus).

    Returns:
        ``"t<tenant_id>"`` for a tenant-owned row, or ``None`` for a shared row — which
        the vector store stores as its explicit null sentinel, never as a dropped key.
    """
    return None if tenant_id is None else f"t{int(tenant_id)}"


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """The isolation boundary one retrieval (or ingest) request runs inside.

    Tenant scope is a **parameter, not a convention**: this value object is threaded
    through the whole retrieval path — cache keys, backend filters, ingest metadata — as
    a single required argument, so a call site cannot accidentally omit it the way an
    optional ``tenant_id=None`` keyword invites. It is frozen and slotted so it can be
    shared freely across a fan-out without any caller mutating another's scope.

    Attributes:
        tenant_id: The governance tenant this request belongs to, or ``None`` for an
            **unscoped** run (offline evals, single-tenant/ungoverned hosts). ``None`` is
            *not* a wildcard: an unscoped request reads only the shared, tenant-less
            corpus, never any tenant's rows.
        persona: The active adapter persona id, or ``None``. Retained from the previous
            contract — it scopes the cache in exactly the same way it always did.
        corpus_version: A monotonically increasing per-tenant counter that changes
            whenever the tenant's corpus does. It is folded into every cache key so an
            ingest invalidates that tenant's cached retrievals *by construction* rather
            than by eviction — otherwise "upload a document, then ask about it" serves
            the pre-upload answer for the whole cache TTL. See
            :mod:`aegis.retrieval.corpus` for the counter itself, and note that **nothing
            increments it yet**: document ingestion arrives in a later phase, and this is
            the seam it plugs into.
    """

    tenant_id: int | None
    persona: str | None = None
    corpus_version: int = 0

    def partition_key(self) -> str:
        """Return a canonical, injective string identifying this scope's cache partition.

        Every field that must never be shared across cache entries is length-prefixed and
        NUL-separated, so no two distinct scopes can produce the same string: a persona
        literally named ``"null"`` is encoded differently from ``persona=None``, and
        ``tenant_id=1`` with ``persona="2"`` cannot collide with ``tenant_id=12``.
        Injectivity is the whole point — a collision here *is* a cross-tenant cache hit.

        Returns:
            The canonical partition string (opaque; only equality and hashing of it are
            meaningful to callers).
        """
        tenant = "-" if self.tenant_id is None else f"={int(self.tenant_id)}"
        persona = "-" if self.persona is None else f"={len(self.persona)}:{self.persona}"
        return f"t{tenant}\x00p{persona}\x00c={int(self.corpus_version)}"

    def tenant_value(self) -> str | None:
        """Return the metadata value marking rows this scope's tenant owns.

        See :func:`tenant_metadata_value` for why it is a string token.
        """
        return tenant_metadata_value(self.tenant_id)

    def visible_tenant_values(self) -> list[str | None]:
        """Return every tenant metadata value this scope is allowed to read.

        The read policy, stated once so both the vector arm and the keyword arm enforce
        the same thing:

        * A **tenant-scoped** request reads its own rows *and* the shared, tenant-less
          corpus (``None``) — the knowledge that belongs to the deployment rather than to
          any one tenant. It can never read another tenant's rows.
        * An **unscoped** request (``tenant_id is None``) reads the shared corpus **only**.
          A null tenant is deliberately not a wildcard: "I have no tenant" must never
          mean "give me everyone's".

        Returns:
            The permitted tenant metadata values, for use as a match-any filter.
        """
        if self.tenant_id is None:
            return [None]
        return [self.tenant_value(), None]


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
