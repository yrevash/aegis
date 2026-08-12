"""A Qdrant-backed vector store — the real ANN engine, never a RAM dict.

This is the single sanctioned vector store for :mod:`aegis.retrieval`. It wraps the
**official** ``qdrant_client.QdrantClient`` in two honest, explicitly-chosen modes:

* **server** — ``QdrantVectorStore.server(url=...)`` for production, talking to a live
  Qdrant node/cluster.
* **local / embedded** — ``QdrantVectorStore.local(path=...)`` (on-disk) or
  ``QdrantVectorStore.local()`` (``location=":memory:"``) for dev and tests. This is the
  client's *own* in-process segment/indexing engine — real HNSW-style search, not a
  hand-rolled brute-force cosine over a Python ``dict``.

The mode is always an explicit constructor choice, never a silent fallback: a store
built for ``server`` that cannot reach its node **raises** (fail loud) rather than
quietly degrading to an in-process index.

``qdrant_client`` is imported lazily (via :func:`aegis.core.lazy.require`) at *construction*
time, so ``import aegis.retrieval`` — and even ``import aegis.retrieval.vector_store`` —
stays cheap and free of the dependency until a store is actually built. A missing package
raises an :class:`ImportError` naming ``aegis[retrieval]``.

Qdrant point ids must be unsigned ints or UUIDs, but our chunk ids are opaque strings
(``"doc-0#3"``). The store therefore maps each ``(collection, string-id)`` pair to a
deterministic UUID5 and stores the original id in the payload under
:data:`_ID_KEY`, so :meth:`search` returns the caller's own ids back — the UUID mapping
is a private implementation detail.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from aegis.core.lazy import require

#: Payload key under which the caller's original (string) id is preserved. Leading
#: underscore keeps it clear of user metadata fields used for filtering.
_ID_KEY = "_id"

#: Namespace for deriving a stable Qdrant point UUID from ``"{collection}:{string_id}"``.
_ID_NAMESPACE = uuid.UUID("a11e5150-0000-4000-8000-000000000001")


def _point_uuid(collection: str, raw_id: str) -> str:
    """Return the deterministic Qdrant point UUID for ``raw_id`` in ``collection``."""
    return str(uuid.uuid5(_ID_NAMESPACE, f"{collection}:{raw_id}"))


@dataclass(frozen=True)
class VectorHit:
    """One search result: the caller's id, the similarity score, and the payload."""

    id: str
    score: float
    payload: dict[str, Any]


class QdrantVectorStore:
    """A thin, honest wrapper over ``qdrant_client.QdrantClient``.

    Construct via :meth:`server` or :meth:`local` rather than ``__init__`` directly, so
    the chosen mode is unambiguous at every call site.
    """

    def __init__(
        self,
        *,
        mode: str,
        url: str | None = None,
        api_key: str | None = None,
        path: str | None = None,
        location: str | None = None,
        timeout: float | None = None,
        prefer_grpc: bool = False,
    ) -> None:
        """Build the underlying client for ``mode`` (fail loud if a server is unreachable).

        Prefer the :meth:`server` / :meth:`local` classmethods; this initialiser exists
        for them and for explicit config-driven wiring.

        Args:
            mode: ``"server"`` or ``"local"`` — recorded on :attr:`mode` for honest logging.
            url: Qdrant server URL (server mode only).
            api_key: Optional API key for a secured server.
            path: On-disk directory for embedded mode (local mode only).
            location: Embedded location such as ``":memory:"`` (local mode only).
            timeout: Optional client timeout in seconds.
            prefer_grpc: Whether to prefer the gRPC transport (server mode).

        Raises:
            ImportError: If ``qdrant_client`` is not installed.
            Exception: In server mode, if the node cannot be reached — never a silent
                downgrade to a local index.
        """
        qdrant = require("aegis[retrieval]", "qdrant_client")
        self._mode = mode
        self._describe = (
            url if mode == "server" else (location or path or ":memory:")
        )
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if mode == "server":
            self._client = qdrant.QdrantClient(
                url=url, api_key=api_key, prefer_grpc=prefer_grpc, **kwargs
            )
            # Fail loud: prove the node is actually reachable now, rather than deferring
            # the failure to the first upsert/search (or worse, degrading silently).
            self._client.get_collections()
        elif mode == "local":
            if path is not None:
                self._client = qdrant.QdrantClient(path=path, **kwargs)
            else:
                self._client = qdrant.QdrantClient(location=location or ":memory:", **kwargs)
        else:  # pragma: no cover - guarded by the classmethods
            raise ValueError(f"unknown QdrantVectorStore mode: {mode!r}")

    @classmethod
    def server(
        cls,
        *,
        url: str,
        api_key: str | None = None,
        timeout: float | None = None,
        prefer_grpc: bool = False,
    ) -> QdrantVectorStore:
        """Build a **production** store against a live Qdrant node (fail loud if down)."""
        return cls(
            mode="server",
            url=url,
            api_key=api_key,
            timeout=timeout,
            prefer_grpc=prefer_grpc,
        )

    @classmethod
    def local(
        cls, *, path: str | None = None, timeout: float | None = None
    ) -> QdrantVectorStore:
        """Build an **embedded** store: on-disk at ``path``, else in-memory (``:memory:``).

        This is the official qdrant-client local engine — a real vector index that runs
        offline with no server — not a homegrown dict scan.
        """
        return cls(mode="local", path=path, timeout=timeout)

    @property
    def mode(self) -> str:
        """The mode this store was built in: ``"server"`` or ``"local"``."""
        return self._mode

    @property
    def location(self) -> str | None:
        """A human-readable description of where vectors live (url / path / ``:memory:``)."""
        return self._describe

    def __repr__(self) -> str:
        """Return an honest, at-a-glance identity string for logs/proofs."""
        return f"QdrantVectorStore(mode={self._mode!r}, location={self._describe!r})"

    def ensure_collection(self, name: str, dim: int, *, distance: str = "Cosine") -> None:
        """Create collection ``name`` (dim ``dim``) if it does not already exist.

        Idempotent: an existing collection is left as-is. Cosine distance is the default
        (embeddings are compared by direction), matching the pipeline's similarity model.

        Args:
            name: Collection name.
            dim: Vector dimensionality.
            distance: Qdrant distance metric name (``"Cosine"``, ``"Dot"``, ``"Euclid"``).
        """
        qdrant = require("aegis[retrieval]", "qdrant_client")
        if self._client.collection_exists(name):
            return
        self._client.create_collection(
            collection_name=name,
            vectors_config=qdrant.models.VectorParams(
                size=dim, distance=qdrant.models.Distance[distance.upper()]
            ),
        )

    def upsert(
        self,
        name: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[Mapping[str, Any]],
    ) -> None:
        """Upsert vectors + payloads under the caller's string ``ids`` into ``name``.

        The original string id is preserved in each payload under :data:`_ID_KEY` and the
        Qdrant point id is a deterministic UUID5 of ``(name, id)`` — so re-upserting the
        same id overwrites in place (idempotent), and :meth:`search` can hand the caller's
        own ids back.

        Args:
            name: Target collection (must already exist — see :meth:`ensure_collection`).
            ids: Caller string ids, aligned with ``vectors`` and ``payloads``.
            vectors: The embedding vectors.
            payloads: Per-point metadata (tenant/subject/doc/text, etc.).
        """
        if not ids:
            return
        qdrant = require("aegis[retrieval]", "qdrant_client")
        points = [
            qdrant.models.PointStruct(
                id=_point_uuid(name, raw_id),
                vector=list(vector),
                payload={**dict(payload), _ID_KEY: raw_id},
            )
            for raw_id, vector, payload in zip(ids, vectors, payloads, strict=True)
        ]
        self._client.upsert(collection_name=name, points=points)

    def search(
        self,
        name: str,
        vector: Sequence[float],
        k: int,
        *,
        filter: Mapping[str, Any] | None = None,  # noqa: A002 - matches store vocabulary
    ) -> list[VectorHit]:
        """Return the ``k`` nearest points to ``vector``, optionally payload-filtered.

        Args:
            name: Collection to search. A missing collection yields ``[]`` (an
                honestly-empty result, e.g. before anything has been ingested).
            vector: The query embedding.
            k: Max results.
            filter: Optional payload scoping as ``{field: value}`` (exact match) or
                ``{field: [v1, v2]}`` (match-any) — used for tenant/subject isolation.

        Returns:
            :class:`VectorHit` records carrying the caller's id, score, and payload,
            best match first.
        """
        if not self._client.collection_exists(name):
            return []
        query_filter = self._build_filter(filter)
        response = self._client.query_points(
            collection_name=name,
            query=list(vector),
            limit=k,
            query_filter=query_filter,
            with_payload=True,
        )
        hits: list[VectorHit] = []
        for point in response.points:
            payload = dict(point.payload or {})
            hits.append(
                VectorHit(
                    id=str(payload.get(_ID_KEY, point.id)),
                    score=float(point.score),
                    payload=payload,
                )
            )
        return hits

    def _build_filter(self, filter: Mapping[str, Any] | None):  # noqa: A002, ANN202
        """Translate a ``{field: value | [values]}`` mapping into a Qdrant ``Filter``."""
        if not filter:
            return None
        qdrant = require("aegis[retrieval]", "qdrant_client")
        conditions = []
        for field, value in filter.items():
            if isinstance(value, (list, tuple, set)):
                match = qdrant.models.MatchAny(any=list(value))
            else:
                match = qdrant.models.MatchValue(value=value)
            conditions.append(qdrant.models.FieldCondition(key=field, match=match))
        return qdrant.models.Filter(must=conditions)

    def close(self) -> None:
        """Close the underlying client (releases an on-disk lock in embedded mode)."""
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
