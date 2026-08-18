"""Pydantic/stdlib-only schema types crossing the retrieval boundary.

Moved out of a host backend's API-schema module (the locked API-contract layer) so
`aegis.retrieval` has no dependency on any host application's schema layer. A host
that wants API-schema identity (so its own request/response models and this
package's never diverge) should re-export these directly rather than redefining
them — see the backend's strangler shim over its schema module.
"""

from __future__ import annotations

import re
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
    "UnresolvedTenantScopeError",
    "tenant_collection_name",
    "tenant_metadata_value",
]

#: The single metadata key under which a row's owning tenant is recorded — on a
#: :class:`~aegis.retrieval.models.Chunk`, on a vector-store payload, and in every
#: ``where`` filter that scopes a search. One name, defined once, so a writer and a
#: reader cannot disagree about where the tenant lives.
TENANT_METADATA_KEY = "tenant_id"

#: The exact shape :func:`tenant_metadata_value` emits. Anything else is refused rather
#: than interpreted — see :func:`tenant_collection_name`.
_TENANT_TOKEN = re.compile(r"t[0-9]+")


class UnresolvedTenantScopeError(LookupError):
    """Raised when a retrieval request's tenant cannot be resolved to a partition.

    The distinction this error draws is the whole safety property of the vector arm.
    ``tenant_id=None`` is *resolved*: it means the shared, tenant-less corpus, and it
    reads exactly that. A scope that is missing entirely, or that carries a tenant id
    which is not an integer — the shape a tenant id has when it arrived from a header, a
    cache key or a half-built request object and lost its type on the way — is
    **unresolved**, and there is no honest partition to read.

    It raises instead of returning an empty result because the two are different facts
    and only one of them is a bug: "this tenant has nothing matching" is an answer, and
    "I do not know whose data to look at" is a defect that must not be able to hide
    behind an empty list. What it must never do is fall back to an unscoped search,
    which is the failure mode the collection-per-tenant layout exists to make
    impossible: an unresolvable scope names no collection, and a collection that does
    not exist holds nothing.
    """


def tenant_collection_name(prefix: str, tenant_value: str | None) -> str:
    """Return the vector-store collection that holds ``tenant_value``'s rows.

    One collection per tenant, rather than one shared collection filtered by a metadata
    ``where`` clause the caller passes. The difference is the direction each fails in. A
    caller who forgets the filter on a shared collection gets **every** tenant's chunks
    and no error; a caller whose scope resolves to a collection that was never created
    gets **nothing**. Same bug, opposite blast radius, and only one of the two is safe
    to ship. It is also the standard multi-tenant vector layout — Pinecone namespaces,
    Qdrant collections, Milvus partitions all draw the boundary in the same place.

    The name is derived from the *bound* scope (or from the row's own recorded owner on
    the write side), never from a caller-supplied argument: a boundary a caller can
    name is a boundary a caller can get wrong.

    Args:
        prefix: The backend's collection-name prefix.
        tenant_value: The canonical tenant metadata token from
            :func:`tenant_metadata_value` — ``"t<id>"``, or ``None`` for the shared,
            tenant-less corpus.

    Returns:
        ``"<prefix>_t<id>"``, or ``"<prefix>_shared"`` for the shared corpus. Tenant ids
        are integers, so a tenant collection can never collide with the shared one.

    Raises:
        UnresolvedTenantScopeError: If ``tenant_value`` is not a token this module
            produced. A name built from an arbitrary string is a partition an attacker
            (or a bug) chooses, which is the property the derivation exists to remove.
    """
    if tenant_value is None:
        return f"{prefix}_shared"
    if not _TENANT_TOKEN.fullmatch(tenant_value):
        raise UnresolvedTenantScopeError(
            f"{tenant_value!r} is not a tenant token produced by "
            "tenant_metadata_value(), so it names no collection. Refusing to derive a "
            "partition name from it."
        )
    return f"{prefix}_{tenant_value}"


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

        Raises:
            UnresolvedTenantScopeError: If the tenant does not resolve. This matters more
                here than anywhere else in this class: the cache is consulted **before**
                the backend is, so a scope carrying ``tenant_id="7"`` would otherwise be
                coerced by ``int()`` into tenant 7's cache partition and served tenant 7's
                answer without a single backend arm ever running. Resolution has to
                happen at the first door, not the second.
        """
        resolved = self.resolved_tenant_id()
        tenant = "-" if resolved is None else f"={resolved}"
        persona = "-" if self.persona is None else f"={len(self.persona)}:{self.persona}"
        return f"t{tenant}\x00p{persona}\x00c={int(self.corpus_version)}"

    def resolved_tenant_id(self) -> int | None:
        """Return this scope's tenant id, having checked that it *is* one.

        ``RetrievalScope`` is a plain frozen dataclass, so nothing stops
        ``RetrievalScope(tenant_id=request.headers.get("x-tenant"))`` from being
        constructed with a string, a sentinel object, or ``True``. Every one of those
        used to flow onward and be formatted into a filter value, where a mismatch
        simply matched nothing on a good day and matched the wrong partition on a bad
        one. Resolution is therefore checked once, here, and every arm asks this rather
        than reading the attribute.

        ``bool`` is rejected explicitly even though it *is* an ``int`` in Python:
        ``tenant_id=True`` would silently resolve to tenant 1, which is a real tenant.

        Returns:
            The tenant id, or ``None`` for the deliberately-unscoped shared corpus.

        Raises:
            UnresolvedTenantScopeError: If the tenant id is present but is not an
                integer — a scope that cannot be resolved must not be searched at all.
        """
        tenant_id = self.tenant_id
        if tenant_id is None:
            return None
        if isinstance(tenant_id, bool) or not isinstance(tenant_id, int):
            raise UnresolvedTenantScopeError(
                f"tenant_id={tenant_id!r} ({type(tenant_id).__name__}) is not a tenant "
                "id, so this request resolves to no partition. Refusing to search: an "
                "unresolved scope must never fall back to an unscoped one."
            )
        return tenant_id

    def tenant_value(self) -> str | None:
        """Return the metadata value marking rows this scope's tenant owns.

        See :func:`tenant_metadata_value` for why it is a string token.

        Raises:
            UnresolvedTenantScopeError: If the tenant does not resolve
                (:meth:`resolved_tenant_id`).
        """
        return tenant_metadata_value(self.resolved_tenant_id())

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
            The permitted tenant metadata values, **own first**, for use as a match-any
            filter or to name the collections this scope may read.

        Raises:
            UnresolvedTenantScopeError: If the tenant does not resolve
                (:meth:`resolved_tenant_id`). Every arm routes through this method, so
                the refusal reaches all of them from one place.
        """
        if self.resolved_tenant_id() is None:
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
