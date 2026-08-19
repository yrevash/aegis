"""A Qdrant-backed vector store — the one vector engine, never a RAM dict.

This is the single sanctioned vector store for :mod:`aegis.retrieval`, and — since
Phase 9 §9.1 — the *only* one Aegis has. It wraps the **official** ``qdrant_client`` in
two honest, explicitly-chosen modes:

* **server** — ``QdrantVectorStore.server(url=...)`` against a running Qdrant node, so
  several processes (``uvicorn --workers N``) see one index. This is the deployment
  profile: Qdrant v1.19.0 ships ``qdrant-x86_64-pc-windows-msvc.zip``, a single Apache-2.0
  binary with no Docker and no installer, which is why a server tier is affordable on the
  locked-down target machine that ADR-0009 wrote embedded-only for.
* **embedded** — ``QdrantVectorStore.local(path=...)`` (on-disk) or
  ``QdrantVectorStore.local()`` (in-process, reported as ``:memory:``) for dev, tests and
  offline evals. ``qdrant_client``'s in-process mode is a real implementation of the same
  API, so a test proves the same call shapes the server path runs.

The mode is always an explicit constructor choice, never a silent fallback: a store
built for ``server`` that cannot reach its node **raises** (fail loud) rather than
quietly degrading to an in-process index.

**Why Qdrant, and why nothing else.** Chroma was deleted rather than demoted. Its
embedded ``PersistentClient`` holds a SQLite metadata lock, and that lock is what makes
``uvicorn --workers 2`` fail today — a ceiling you can still *configure* your way back
into is not removed. It also could not serve the other consumer: LightRAG 1.5.6 has no
``chroma_impl`` module at all, so keeping Chroma would have meant running two vector
systems. Qdrant serves both (``lightrag.kg.qdrant_impl.QdrantVectorDBStorage`` reads the
same ``QDRANT_URL``), which leaves Aegis with one vector engine, one operational story
and one thing to install.

**The embedded modes still exist, and are still guarded.** An embedded store is a
single-process store whichever engine backs it, so building one in a process that was
asked for more than one worker raises :class:`EmbeddedVectorStoreMultiprocessError` at
construction — see :func:`refuse_embedded_multiprocess`. That is the property "scaling
later is a deployment change" needs in order to be true rather than aspirational.

``qdrant_client`` is imported lazily (via :func:`aegis.core.lazy.require`) at
*construction* time, so ``import aegis.retrieval`` — and even
``import aegis.retrieval.vector_store`` — stays cheap and free of the dependency until a
store is actually built. A missing package raises an :class:`ImportError` naming
``aegis[retrieval]``.

Three Qdrant constraints are handled here rather than leaked to callers:

* **Point ids must be UUIDs or unsigned ints.** The caller's own ids (``"doc-0#3"``) are
  neither, so each is mapped through a *deterministic* ``uuid5`` and the original is
  mirrored into the payload under :data:`_ID_KEY`. Determinism is the load-bearing part:
  re-upserting the same caller id **replaces** that row instead of duplicating it, and
  :meth:`QdrantVectorStore.search` hands the caller's own id back. (This is the same
  scheme LightRAG's own Qdrant implementation uses.)
* **No ``None`` in a match filter.** A payload ``None`` is stored as JSON null, and
  Qdrant's ``MatchValue`` cannot express null — a naive encoding would have to *drop* the
  condition, which turns "null tenant" into "any tenant": a cross-tenant leak, not a
  cosmetic bug. Every ``None`` is therefore stored as the explicit :data:`_NULL` sentinel
  and decoded back to ``None`` on the way out, so ``{"tenant_id": None}`` is a *positive,
  exact* match on null-tenant rows only. Storing the sentinel string itself as a real
  value raises rather than aliasing.
* **Collection names** become directory names in the embedded on-disk mode, so they are
  normalised by :func:`_safe_name` onto ``[a-zA-Z0-9._-]`` (3–512 chars, alphanumeric at
  both ends), injectively — an out-of-spec name gets a slug plus a hash of the original,
  so callers keep using their own vocabulary and a one-character name no longer risks a
  collision or a rejected path.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from aegis.core.lazy import require

#: Payload key under which the caller's original (string) id is preserved. Leading
#: underscore keeps it clear of user payload fields used for filtering.
_ID_KEY = "_id"

#: Namespace for the deterministic caller-id → point-id mapping. A fixed namespace is
#: what makes the mapping stable across processes and restarts, which is what makes an
#: upsert idempotent rather than duplicating.
_ID_NAMESPACE = uuid.UUID("6f2f0a1e-8a2e-5b7d-9c4f-2f1e6a3b7c5d")

#: Stored stand-in for a ``None`` payload value. Qdrant stores JSON null happily but
#: cannot *match* it with ``MatchValue``, which would erase the difference between "null
#: tenant" and "no tenant filter"; this sentinel keeps the null scope addressable and
#: exact. U+E000 is a private-use codepoint, so the string cannot occur in real data —
#: and :func:`_encode_value` raises if it ever does.
_NULL = "aegis:null"

#: Caller-facing distance names mapped onto Qdrant's metric names. Kept as plain strings
#: so this module has no import-time dependency on ``qdrant_client``.
_SPACES = {"COSINE": "Cosine", "DOT": "Dot", "EUCLID": "Euclid"}

#: A collection name that is safe as both a Qdrant collection and an on-disk directory:
#: 3–512 of ``[a-zA-Z0-9._-]``, alnum at both ends.
_LEGAL_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{1,510}[a-zA-Z0-9]")

#: Characters not allowed inside a collection name.
_ILLEGAL_CHARS = re.compile(r"[^a-zA-Z0-9._-]")

#: Environment variables a process manager uses to ask for more than one worker.
_WORKER_ENV_VARS = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS")


def _safe_name(name: str) -> str:
    """Return a Qdrant-legal collection name for ``name``, injectively.

    A name already legal is returned unchanged, so ``"aegis_lite_chunks"`` and
    ``"aegis_mem_memory_fact_d4"`` keep their readable identities on the wire and on
    disk. Anything else (too short, or carrying characters outside ``[a-zA-Z0-9._-]``) is
    rewritten as ``aegis-<slug>-<sha1 prefix of the original>``: readable, always legal,
    and distinct for distinct inputs because the digest is taken over the *original* name.
    """
    if _LEGAL_NAME.fullmatch(name):
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    slug = _ILLEGAL_CHARS.sub("-", name)[:64].strip("._-")
    return f"aegis-{slug}-{digest}" if slug else f"aegis-{digest}"


def _point_id(raw_id: str) -> str:
    """Return the Qdrant point id for a caller's string ``raw_id``.

    Deterministic on purpose: Qdrant accepts only UUIDs and unsigned ints as point ids,
    so an arbitrary caller id has to be mapped — and a *random* mapping would make every
    re-upsert a duplicate instead of a replacement.
    """
    return uuid.uuid5(_ID_NAMESPACE, str(raw_id)).hex


def _encode_value(key: str, value: Any) -> str | int | float | bool:  # noqa: ANN401
    """Return ``value`` in a form the store can filter on, mapping ``None`` to :data:`_NULL`.

    Raises:
        ValueError: If a caller tries to store the reserved null sentinel literally —
            silently aliasing it onto ``None`` would corrupt tenant scoping.
        TypeError: If the value is not a storable scalar.
    """
    if value is None:
        return _NULL
    if isinstance(value, str):
        if value == _NULL:
            raise ValueError(
                f"payload {key!r} uses the reserved null sentinel; it cannot be stored"
            )
        return value
    if isinstance(value, (bool, int, float)):
        return value
    raise TypeError(
        f"payload {key!r} must be str/int/float/bool/None, got {type(value).__name__}"
    )


def _decode_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return ``payload`` with the null sentinel turned back into ``None``."""
    return {k: (None if v == _NULL else v) for k, v in (payload or {}).items()}


@dataclass(frozen=True)
class VectorHit:
    """One search result: the caller's id, the similarity score, and the payload."""

    id: str
    score: float
    payload: dict[str, Any]


class EmbeddedVectorStoreMultiprocessError(RuntimeError):
    """An embedded vector store was built in a process asked to run several workers.

    An embedded store — Qdrant's in-process client, and Chroma's ``PersistentClient``
    before it — is a **single-process** store: a second worker opening the same storage
    either fails on the lock or, worse, corrupts the index while every health check stays
    green. Refusing at construction turns that into one sentence at boot.
    """


def configured_worker_count() -> tuple[int, str] | None:
    """Return ``(workers, source)`` if this process was asked to run more than one.

    Reads the two places the answer can actually come from, and names which one it read:

    * the command line (``uvicorn --workers 4``, ``--workers=4``, ``gunicorn -w 4``),
    * the environment (``WEB_CONCURRENCY``, ``UVICORN_WORKERS``, ``GUNICORN_WORKERS``).

    Returns ``None`` when nothing asked for more than one worker — including the ordinary
    test/dev case, where neither source is set at all.
    """
    argv = list(sys.argv[1:])
    for index, token in enumerate(argv):
        raw: str | None = None
        if token.startswith("--workers="):
            raw = token.split("=", 1)[1]
        elif token in ("--workers", "-w") and index + 1 < len(argv):
            raw = argv[index + 1]
        if raw is not None:
            try:
                workers = int(raw)
            except ValueError:
                continue
            if workers > 1:
                return workers, f"{token} on the command line"
    for name in _WORKER_ENV_VARS:
        value = os.environ.get(name)
        if not value:
            continue
        try:
            workers = int(value)
        except ValueError:
            continue
        if workers > 1:
            return workers, f"{name}={value}"
    return None


def refuse_embedded_multiprocess(location: str) -> None:
    """Raise if an embedded store is being built in a multi-worker process (§9.1).

    Called from :class:`QdrantVectorStore` for both embedded modes, and from the runtime
    composition root before it *declares* an embedded factory, so the refusal lands at
    boot rather than on the first request that happened to need an index.

    Args:
        location: What the embedded store would have opened (a path, or ``":memory:"``),
            so the message names the thing that has to change.

    Raises:
        EmbeddedVectorStoreMultiprocessError: If more than one worker was requested. The
            message names the worker count, where that number came from, and the fix.
    """
    asked = configured_worker_count()
    if asked is None:
        return
    workers, source = asked
    raise EmbeddedVectorStoreMultiprocessError(
        f"This process was asked for {workers} workers ({source}) but the vector store "
        f"is EMBEDDED (at {location!r}), and an embedded store is single-process: the "
        "second worker either fails on the storage lock or silently diverges from the "
        "first worker's index while every health check stays green. Run one Qdrant node "
        "and point Aegis at it — AEGIS_VECTOR_STORE_URL=http://localhost:6333 (QDRANT_URL "
        "is read too, so LightRAG and Aegis share one value) — or drop back to a single "
        "worker. Aegis refuses to boot into the configuration that corrupts the index."
    )


class QdrantVectorStore:
    """A thin, honest wrapper over the official ``qdrant_client``.

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
    ) -> None:
        """Build the underlying client for ``mode`` (fail loud if a server is unreachable).

        Prefer the :meth:`server` / :meth:`local` classmethods; this initialiser exists
        for them and for explicit config-driven wiring.

        Args:
            mode: ``"server"`` or ``"local"`` — recorded on :attr:`mode` for honest logging.
            url: Qdrant node URL, e.g. ``"http://localhost:6333"`` (server mode only).
            api_key: Optional token for a secured node.
            path: On-disk directory for the embedded mode (local mode only).
            location: Embedded location override; only ``":memory:"`` (or ``None``, which
                means the same thing) is meaningful.
            timeout: Optional request timeout in seconds (server mode; ``qdrant_client``
                takes whole seconds).

        Raises:
            ImportError: If ``qdrant_client`` is not installed.
            ValueError: If ``mode`` is unknown, or ``server`` mode is given no url.
            EmbeddedVectorStoreMultiprocessError: If an embedded mode is asked for in a
                process configured for more than one worker (§9.1).
            Exception: In server mode, whatever the client raises when the node cannot be
                reached. Never a silent downgrade to an in-process index.
        """
        qdrant = require("aegis[retrieval]", "qdrant_client")
        self._mode = mode
        #: Vector width of every collection this store has already proven exists, so the
        #: hot path does not re-ask on every upsert. A miss always re-queries the server.
        #: The *width* is cached rather than bare existence because the conflicting-dim
        #: check has to keep firing after the first successful ensure, not before it.
        self._known: dict[str, int] = {}

        if mode == "server":
            if not url:
                raise ValueError("server mode needs a url")
            self._describe = url
            # ``qdrant_client`` takes whole seconds; round up so a sub-second request
            # never becomes a zero-second (i.e. immediate-failure) one.
            seconds = None if timeout is None else max(1, int(timeout + 0.999))
            self._client = qdrant.QdrantClient(url=url, api_key=api_key, timeout=seconds)
            # Fail loud: prove the node is actually reachable now, rather than deferring
            # the failure to the first upsert/search (or worse, degrading silently).
            self._client.get_collections()
        elif mode == "local":
            described = path if path is not None else (location or ":memory:")
            refuse_embedded_multiprocess(described)
            self._describe = described
            if path is not None:
                self._client = qdrant.QdrantClient(path=path)
            else:
                self._client = qdrant.QdrantClient(":memory:")
        else:
            raise ValueError(f"unknown QdrantVectorStore mode: {mode!r}")

    @classmethod
    def server(
        cls,
        *,
        url: str,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> QdrantVectorStore:
        """Build a store against a live Qdrant node (fail loud if it is down)."""
        return cls(mode="server", url=url, api_key=api_key, timeout=timeout)

    @classmethod
    def local(
        cls, *, path: str | None = None, timeout: float | None = None
    ) -> QdrantVectorStore:
        """Build an **embedded** store: on-disk at ``path``, else in-process (``:memory:``).

        This is ``qdrant_client``'s own in-process implementation of the same API the
        server speaks, so an offline test exercises the real call shapes. It is
        deliberately single-process: :func:`refuse_embedded_multiprocess` raises if this
        process was asked for more than one worker.

        ``timeout`` is accepted for signature parity with :meth:`server` and has no effect
        here: an embedded call is a direct in-process call, with no request to time out.
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

    # ------------------------------------------------------------------ collections

    def _dim_of(self, key: str) -> int | None:
        """Return the vector width collection ``key`` was created for, or ``None``."""
        if not self._client.collection_exists(key):
            return None
        params = self._client.get_collection(key).config.params.vectors
        size = getattr(params, "size", None)
        return None if size is None else int(size)

    def ensure_collection(self, name: str, dim: int, *, distance: str = "Cosine") -> None:
        """Create collection ``name`` (dim ``dim``) if it does not already exist.

        Idempotent: an existing collection is left as-is. Cosine distance is the default
        (embeddings are compared by direction), matching the pipeline's similarity model.

        Qdrant records the vector width natively, so it is re-checked here against the
        collection itself: asking for the same collection at a *different* dim fails
        loudly instead of writing mixed-width vectors into one index.

        Args:
            name: Collection name (normalised via :func:`_safe_name` if it would be
                illegal as a collection or an on-disk directory).
            dim: Vector dimensionality.
            distance: Distance metric (``"Cosine"``, ``"Dot"``, ``"Euclid"``).

        Raises:
            ValueError: If ``distance`` is unknown, or the collection already exists with
                a different vector width.
        """
        metric = _SPACES.get(distance.upper())
        if metric is None:
            raise ValueError(
                f"unknown distance {distance!r}; expected one of {sorted(_SPACES)}"
            )
        key = _safe_name(name)
        recorded = self._known.get(key)
        if recorded is None:
            recorded = self._dim_of(key)
        if recorded is not None:
            if recorded != int(dim):
                raise ValueError(
                    f"collection {name!r} already holds {recorded}-dim vectors, "
                    f"not {dim}-dim"
                )
            self._known[key] = recorded
            return
        models = require("aegis[retrieval]", "qdrant_client.models")
        try:
            self._client.create_collection(
                collection_name=key,
                vectors_config=models.VectorParams(
                    size=int(dim), distance=models.Distance(metric)
                ),
            )
        except Exception:  # noqa: BLE001 - another writer may have won the race
            # Adopt what the winner created, but only if it is genuinely the same shape;
            # a real failure re-raises through the dimension check below.
            recorded = self._dim_of(key)
            if recorded is None:
                raise
            if recorded != int(dim):
                raise ValueError(
                    f"collection {name!r} already holds {recorded}-dim vectors, "
                    f"not {dim}-dim"
                ) from None
        self._known[key] = int(dim)

    # ------------------------------------------------------------------ read / write

    def upsert(
        self,
        name: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[Mapping[str, Any]],
    ) -> None:
        """Upsert vectors + payloads under the caller's string ``ids`` into ``name``.

        The caller's id *is* the primary key: it is mapped deterministically onto a
        Qdrant point id (see :func:`_point_id`), so re-upserting the same id **replaces**
        that row rather than duplicating it (idempotent), and :meth:`search` hands the
        caller's own ids back. The id is mirrored into the payload under :data:`_ID_KEY`.

        Args:
            name: Target collection (must already exist — see :meth:`ensure_collection`).
            ids: Caller string ids, aligned with ``vectors`` and ``payloads``.
            vectors: The embedding vectors.
            payloads: Per-point metadata (tenant/subject/doc/text, etc.). ``None`` values
                are stored as an explicit null sentinel, so a null scope stays exactly
                matchable rather than becoming unfilterable.

        Raises:
            LookupError: If the collection does not exist yet.
        """
        if not ids:
            return
        key = _safe_name(name)
        if key not in self._known:
            existing = self._dim_of(key)
            if existing is None:
                raise LookupError(
                    f"collection {name!r} does not exist; call ensure_collection() first"
                )
            self._known[key] = existing
        models = require("aegis[retrieval]", "qdrant_client.models")
        points = [
            models.PointStruct(
                id=_point_id(raw_id),
                vector=[float(component) for component in vector],
                payload={
                    **{k: _encode_value(k, v) for k, v in dict(payload).items()},
                    _ID_KEY: str(raw_id),
                },
            )
            for raw_id, vector, payload in zip(ids, vectors, payloads, strict=True)
        ]
        self._client.upsert(collection_name=key, points=points, wait=True)

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
                A ``None`` value matches null-scoped points **only**, never every point.

        Returns:
            :class:`VectorHit` records carrying the caller's id, score, and payload,
            best match first. Scores are similarities (higher is better), so a cosine
            collection returns ``1.0`` for an exact match — the same convention the
            pipeline scored on before.
        """
        if k <= 0:
            return []
        key = _safe_name(name)
        if key not in self._known:
            existing = self._dim_of(key)
            if existing is None:
                return []
            self._known[key] = existing
        metric = str(
            getattr(
                self._client.get_collection(key).config.params.vectors, "distance", "Cosine"
            )
        )
        response = self._client.query_points(
            collection_name=key,
            query=[float(component) for component in vector],
            limit=k,
            with_payload=True,
            query_filter=self._build_filter(filter),
        )
        hits: list[VectorHit] = []
        for point in response.points:
            payload = _decode_payload(point.payload)
            hits.append(
                VectorHit(
                    id=str(payload.get(_ID_KEY, point.id)),
                    score=_score(metric, float(point.score)),
                    payload=payload,
                )
            )
        return hits

    def _build_filter(  # noqa: ANN202
        self, filter: Mapping[str, Any] | None  # noqa: A002 - matches store vocabulary
    ):
        """Translate a ``{field: value | [values]}`` mapping into a Qdrant ``Filter``.

        ``None`` is encoded to the null sentinel, so an unscoped-tenant filter selects
        exactly the null-tenant points instead of the condition having to be dropped
        (Qdrant cannot match a JSON null with ``MatchValue``, and a dropped condition is
        how a tenant leak starts).
        """
        if not filter:
            return None
        models = require("aegis[retrieval]", "qdrant_client.models")
        conditions = []
        for field, value in filter.items():
            if isinstance(value, (list, tuple, set)):
                match = models.MatchAny(any=[_encode_value(field, v) for v in value])
            else:
                match = models.MatchValue(value=_encode_value(field, value))
            conditions.append(models.FieldCondition(key=field, match=match))
        return models.Filter(must=conditions)

    def close(self) -> None:
        """Close the underlying client (releases the storage lock in embedded mode)."""
        self._known.clear()
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


# --------------------------------------------------------------------------- helpers


def _score(metric: str, score: float) -> float:
    """Convert a Qdrant ``score`` into a similarity (higher is better).

    Cosine and Dot are already similarities and pass through unchanged — a
    cosine-identical vector scores ``1.0``, which is the convention the pipeline and the
    memory composite score are written against. Euclid is a *distance*, so it is negated
    to keep "higher is better" true for ranking.
    """
    return -score if metric.lower().endswith("euclid") else score


# ─────────────────────────────────────────────────────────────────────────── seam
# The process-wide vector-store choice — configured out loud, never guessed.
#
# Phase 8 §8.4. ``InMemoryKnowledgeBackend`` used to build an embedded store for any
# caller that handed it none, which meant a host that simply forgot to wire a durable
# store got a *working-looking* system whose vectors evaporated on restart, with no log
# line and no error to follow. That is the one failure mode a first-attempt integration
# cannot recover from, so the default is gone: a component that needs a store and was
# given none now asks here, and this raises unless the process said which engine it wants.
#
# A **factory**, not a store instance, on purpose: the embedded modes are per-process
# handles (an on-disk one takes an exclusive lock, an in-process one holds its own data),
# so handing several components one shared instance would couple their lifetimes. The
# factory is called once per component that needs a store.
_STORE_FACTORY: Callable[[], QdrantVectorStore] | None = None


class VectorStoreNotConfiguredError(RuntimeError):
    """No vector store was configured, and the component that needs one will not guess.

    Raised by :func:`new_default_store` — and, through it, by any component built without
    an explicit ``vector_store=``. It is deliberately a hard failure: the alternative
    (an ephemeral in-process index) is indistinguishable from a working deployment until
    the first restart loses every vector.
    """


def configure_vector_store(factory: Callable[[], QdrantVectorStore] | None) -> None:
    """Declare which vector store components build when they are handed none.

    Call once at host startup, next to the other ``configure_*`` seams. The two honest
    choices are both explicit::

        configure_vector_store(lambda: QdrantVectorStore.server(url=settings.url))  # durable
        configure_vector_store(QdrantVectorStore.local)                             # ephemeral

    Args:
        factory: A zero-argument callable returning a fresh
            :class:`QdrantVectorStore`; ``None`` clears the declaration (the next
            component that needs a store raises :class:`VectorStoreNotConfiguredError`).
    """
    global _STORE_FACTORY  # noqa: PLW0603 - a single deliberate configuration seam
    _STORE_FACTORY = factory


def reset_vector_store() -> None:
    """Clear the configured factory (test teardown; the next use raises again)."""
    configure_vector_store(None)


def new_default_store() -> QdrantVectorStore:
    """Build a fresh store from the configured factory, or fail loud.

    Returns:
        A new :class:`QdrantVectorStore` from the factory given to
        :func:`configure_vector_store`.

    Raises:
        VectorStoreNotConfiguredError: If nothing was configured. The message names the
            call to make and both honest values for it — a skipped step has to point at
            its own fix, because the integrator has nothing else to go on.
    """
    if _STORE_FACTORY is None:
        raise VectorStoreNotConfiguredError(
            "aegis.retrieval has no vector store configured, so this component has no "
            "index to write to. Call "
            "aegis.retrieval.configure_vector_store(lambda: QdrantVectorStore.server("
            "url=...)) at startup for a DURABLE, multi-process Qdrant node, or "
            "configure_vector_store(QdrantVectorStore.local) to choose the EPHEMERAL "
            "in-process engine explicitly (dev/tests/offline evals) — or pass "
            "vector_store=... to this component. It used to build the ephemeral store "
            "for you, which made a forgotten call look exactly like a working system "
            "until a restart lost every vector."
        )
    return _STORE_FACTORY()
