"""A ChromaDB-backed vector store — the real ANN engine, never a RAM dict.

This is the single sanctioned vector store for :mod:`aegis.retrieval`. It wraps the
**official** ``chromadb`` client in two honest, explicitly-chosen modes:

* **server** — ``ChromaVectorStore.server(url=...)`` for a deployment that runs a shared
  Chroma server (``chromadb.HttpClient``), so several processes see one index.
* **local / embedded** — ``ChromaVectorStore.local(path=...)`` (on-disk
  ``PersistentClient``) or ``ChromaVectorStore.local()`` (in-process
  ``EphemeralClient``, reported as ``:memory:``) for dev and tests.

The mode is always an explicit constructor choice, never a silent fallback: a store
built for ``server`` that cannot reach its node **raises** (fail loud) rather than
quietly degrading to an in-process index.

Why Chroma rather than Qdrant: the embedded mode is *pure Python + hnswlib/rust
bindings* — a real on-disk HNSW index with **no server binary to install**. Qdrant's
embedded mode still needed a native server story that a locked-down target machine
cannot provide. Embedded Chroma is genuinely the same engine as the server one, just
in-process; both modes are real ANN, and neither is a hand-rolled cosine loop over a
Python ``dict``.

``chromadb`` is imported lazily (via :func:`aegis.core.lazy.require`) at *construction*
time, so ``import aegis.retrieval`` — and even ``import aegis.retrieval.vector_store`` —
stays cheap and free of the dependency until a store is actually built. A missing package
raises an :class:`ImportError` naming ``aegis[retrieval]``.

Two Chroma constraints are handled here rather than leaked to callers:

* **No ``None`` metadata values.** Chroma silently *drops* a key whose value is ``None``,
  which would make a null tenant indistinguishable from "any tenant" in a ``where``
  filter — a cross-tenant leak, not a cosmetic bug. Every ``None`` is therefore stored as
  the explicit :data:`_NULL` sentinel and decoded back to ``None`` on the way out, so
  ``{"tenant_id": None}`` is a *positive, exact* match on null-tenant rows only. Storing
  the sentinel string itself as a real value raises rather than aliasing.
* **Collection names** must be 3–512 chars from ``[a-zA-Z0-9._-]`` starting and ending
  alphanumeric. :func:`_safe_name` maps any caller name onto a legal one, injectively
  (an out-of-spec name gets a slug plus a hash of the original), so callers keep using
  their own vocabulary and a one-character name no longer raises.

Chroma point ids are arbitrary strings, so the caller's own ids (``"doc-0#3"``) are used
verbatim as the collection-scoped primary key: re-upserting the same id **replaces** that
row instead of duplicating it, and ids in different collections never collide. The
caller's id is also mirrored into the metadata under :data:`_ID_KEY`, which additionally
guarantees the metadata dict is never empty (Chroma rejects ``{}``).
"""

from __future__ import annotations

import hashlib
import re
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from aegis.core.lazy import require

#: Metadata key under which the caller's original (string) id is preserved. Leading
#: underscore keeps it clear of user metadata fields used for filtering. It also keeps
#: every metadata dict non-empty, which Chroma requires.
_ID_KEY = "_id"

#: Metadata key recording the dimensionality a collection was created for.
_DIM_KEY = "aegis:dim"

#: Stored stand-in for a ``None`` metadata value. Chroma drops ``None`` outright, which
#: would erase the difference between "null tenant" and "no tenant filter"; this sentinel
#: keeps the null scope addressable and exact. U+E000 is a private-use codepoint, so the
#: string cannot occur in real data — and :func:`_encode_value` raises if it ever does.
_NULL = "\ue000aegis:null"

#: Caller-facing distance names mapped onto Chroma's HNSW space names.
_SPACES = {"COSINE": "cosine", "DOT": "ip", "EUCLID": "l2"}

#: A collection name Chroma accepts as-is: 3–512 of ``[a-zA-Z0-9._-]``, alnum at both ends.
_LEGAL_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{1,510}[a-zA-Z0-9]")

#: Characters Chroma forbids inside a collection name.
_ILLEGAL_CHARS = re.compile(r"[^a-zA-Z0-9._-]")

#: Guards the lazily-built admin client used to carve out per-store ephemeral databases.
_ADMIN_LOCK = threading.Lock()
_ADMIN: Any = None


def _safe_name(name: str) -> str:
    """Return a Chroma-legal collection name for ``name``, injectively.

    A name Chroma already accepts is returned unchanged, so ``"aegis_lite_chunks"`` and
    ``"aegis_mem_memory_fact_d4"`` keep their readable identities on disk. Anything else
    (too short, or carrying characters outside ``[a-zA-Z0-9._-]``) is rewritten as
    ``aegis-<slug>-<sha1 prefix of the original>``: readable, always legal, and distinct
    for distinct inputs because the digest is taken over the *original* name.
    """
    if _LEGAL_NAME.fullmatch(name):
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    slug = _ILLEGAL_CHARS.sub("-", name)[:64].strip("._-")
    return f"aegis-{slug}-{digest}" if slug else f"aegis-{digest}"


def _encode_value(key: str, value: Any) -> str | int | float | bool:  # noqa: ANN401
    """Return ``value`` in a form Chroma can store, mapping ``None`` to :data:`_NULL`.

    Raises:
        ValueError: If a caller tries to store the reserved null sentinel literally —
            silently aliasing it onto ``None`` would corrupt tenant scoping.
        TypeError: If the value is not a Chroma-storable scalar.
    """
    if value is None:
        return _NULL
    if isinstance(value, str):
        if value == _NULL:
            raise ValueError(
                f"metadata {key!r} uses the reserved null sentinel; it cannot be stored"
            )
        return value
    if isinstance(value, (bool, int, float)):
        return value
    raise TypeError(
        f"metadata {key!r} must be str/int/float/bool/None, got {type(value).__name__}"
    )


def _decode_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return ``metadata`` with the null sentinel turned back into ``None``."""
    return {k: (None if v == _NULL else v) for k, v in (metadata or {}).items()}


@dataclass(frozen=True)
class VectorHit:
    """One search result: the caller's id, the similarity score, and the payload."""

    id: str
    score: float
    payload: dict[str, Any]


class ChromaVectorStore:
    """A thin, honest wrapper over the official ``chromadb`` client.

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
            url: Chroma server URL, e.g. ``"http://chroma:8000"`` (server mode only).
            api_key: Optional token for a secured server (sent as a bearer token).
            path: On-disk directory for embedded mode (local mode only).
            location: Accepted for config-driven parity; only ``":memory:"`` (or ``None``)
                is meaningful for Chroma's embedded client, which is either on-disk at
                ``path`` or in-process.
            timeout: Optional request timeout in seconds (server mode; Chroma exposes it
                at whole-second granularity).

        Raises:
            ImportError: If ``chromadb`` is not installed.
            ValueError: If ``mode`` is unknown, or — in server mode — if the node cannot
                be reached. Never a silent downgrade to an in-process index.
        """
        chroma = require("aegis[retrieval]", "chromadb")
        config = require("aegis[retrieval]", "chromadb.config")
        self._mode = mode
        #: Collection handles keyed by their Chroma-legal name; a miss re-queries, so a
        #: collection another store created later is still found.
        self._collections: dict[str, Any] = {}

        settings = config.Settings(anonymized_telemetry=False)
        if mode == "server":
            if not url:
                raise ValueError("server mode needs a url")
            host, port, ssl = _split_url(url)
            settings.chroma_server_ssl_enabled = ssl
            if timeout is not None:
                # Chroma's HTTP client only exposes a whole-second query timeout; round
                # up so a sub-second request never becomes a zero-second one.
                settings.chroma_query_request_timeout_seconds = max(1, int(timeout + 0.999))
            if api_key:
                settings.chroma_client_auth_provider = (
                    "chromadb.auth.token_authn.TokenAuthClientProvider"
                )
                settings.chroma_client_auth_credentials = api_key
            self._describe = url
            self._client = chroma.HttpClient(host=host, port=port, ssl=ssl, settings=settings)
            # Fail loud: prove the node is actually reachable now, rather than deferring
            # the failure to the first upsert/search (or worse, degrading silently).
            self._client.heartbeat()
        elif mode == "local":
            if path is not None:
                self._describe = path
                self._client = chroma.PersistentClient(path=path, settings=settings)
            else:
                # Chroma keys its embedded system by persist directory, so *every*
                # in-process client shares one engine. Carving a fresh database out of it
                # per store keeps two ``local()`` stores genuinely isolated (and stops
                # points bleeding between tests) while still using the real engine.
                self._describe = location or ":memory:"
                database = _ephemeral_database(chroma, config, settings)
                self._client = chroma.EphemeralClient(
                    settings=settings, tenant=config.DEFAULT_TENANT, database=database
                )
        else:
            raise ValueError(f"unknown ChromaVectorStore mode: {mode!r}")

    @classmethod
    def server(
        cls,
        *,
        url: str,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> ChromaVectorStore:
        """Build a store against a live Chroma server (fail loud if it is down)."""
        return cls(mode="server", url=url, api_key=api_key, timeout=timeout)

    @classmethod
    def local(
        cls, *, path: str | None = None, timeout: float | None = None
    ) -> ChromaVectorStore:
        """Build an **embedded** store: on-disk at ``path``, else in-process (``:memory:``).

        This is the official chromadb embedded engine — a real HNSW index that runs
        offline with **no server process at all** — not a homegrown dict scan.

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
        return f"ChromaVectorStore(mode={self._mode!r}, location={self._describe!r})"

    # ------------------------------------------------------------------ collections

    def _lookup(self, name: str) -> Any | None:  # noqa: ANN401 - a chromadb Collection
        """Return the handle for collection ``name``, or ``None`` if it does not exist."""
        errors = require("aegis[retrieval]", "chromadb.errors")
        key = _safe_name(name)
        cached = self._collections.get(key)
        if cached is not None:
            return cached
        try:
            collection = self._client.get_collection(key)
        except errors.NotFoundError:
            return None
        self._collections[key] = collection
        return collection

    def ensure_collection(self, name: str, dim: int, *, distance: str = "Cosine") -> None:
        """Create collection ``name`` (dim ``dim``) if it does not already exist.

        Idempotent: an existing collection is left as-is. Cosine distance is the default
        (embeddings are compared by direction), matching the pipeline's similarity model.

        The dimensionality is recorded in the collection metadata and re-checked here, so
        asking for the same collection at a *different* dim fails loudly instead of
        writing mixed-width vectors into one index.

        Args:
            name: Collection name (normalised via :func:`_safe_name` if Chroma would
                reject it).
            dim: Vector dimensionality.
            distance: Distance metric (``"Cosine"``, ``"Dot"``, ``"Euclid"``).

        Raises:
            ValueError: If ``distance`` is unknown, or the collection already exists with
                a different recorded dimensionality.
        """
        space = _SPACES.get(distance.upper())
        if space is None:
            raise ValueError(
                f"unknown distance {distance!r}; expected one of {sorted(_SPACES)}"
            )
        existing = self._lookup(name)
        if existing is not None:
            recorded = (existing.metadata or {}).get(_DIM_KEY)
            if recorded is not None and int(recorded) != int(dim):
                raise ValueError(
                    f"collection {name!r} already holds {recorded}-dim vectors, "
                    f"not {dim}-dim"
                )
            return
        errors = require("aegis[retrieval]", "chromadb.errors")
        key = _safe_name(name)
        try:
            collection = self._client.create_collection(
                name=key,
                metadata={"hnsw:space": space, _DIM_KEY: int(dim)},
                # Vectors are always supplied explicitly; never let Chroma reach for its
                # default ONNX embedder (a model download, and wrong for our pipeline).
                embedding_function=None,
            )
        except (errors.UniqueConstraintError, errors.InternalError):
            # Another store on the same path won the race — adopt what it created.
            collection = self._client.get_collection(key)
        self._collections[key] = collection

    # ------------------------------------------------------------------ read / write

    def upsert(
        self,
        name: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[Mapping[str, Any]],
    ) -> None:
        """Upsert vectors + payloads under the caller's string ``ids`` into ``name``.

        Chroma ids are arbitrary strings scoped to their collection, so the caller's own
        id *is* the primary key: re-upserting the same id **replaces** that row rather
        than duplicating it (idempotent), and :meth:`search` hands the caller's own ids
        back. The id is mirrored into the metadata under :data:`_ID_KEY` as well, which
        also keeps the metadata dict non-empty (Chroma rejects ``{}``).

        Args:
            name: Target collection (must already exist — see :meth:`ensure_collection`).
            ids: Caller string ids, aligned with ``vectors`` and ``payloads``.
            vectors: The embedding vectors.
            payloads: Per-point metadata (tenant/subject/doc/text, etc.). ``None`` values
                are stored as an explicit null sentinel, so a null scope stays exactly
                matchable rather than vanishing.

        Raises:
            LookupError: If the collection does not exist yet.
        """
        if not ids:
            return
        collection = self._lookup(name)
        if collection is None:
            raise LookupError(
                f"collection {name!r} does not exist; call ensure_collection() first"
            )
        metadatas = [
            {
                **{k: _encode_value(k, v) for k, v in dict(payload).items()},
                _ID_KEY: raw_id,
            }
            for raw_id, payload in zip(ids, payloads, strict=True)
        ]
        collection.upsert(
            ids=[str(raw_id) for raw_id in ids],
            embeddings=[list(vector) for vector in vectors],
            metadatas=metadatas,
        )

    def search(
        self,
        name: str,
        vector: Sequence[float],
        k: int,
        *,
        filter: Mapping[str, Any] | None = None,  # noqa: A002 - matches store vocabulary
    ) -> list[VectorHit]:
        """Return the ``k`` nearest points to ``vector``, optionally metadata-filtered.

        Args:
            name: Collection to search. A missing collection yields ``[]`` (an
                honestly-empty result, e.g. before anything has been ingested).
            vector: The query embedding.
            k: Max results.
            filter: Optional metadata scoping as ``{field: value}`` (exact match) or
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
        collection = self._lookup(name)
        if collection is None:
            return []
        response = collection.query(
            query_embeddings=[list(vector)],
            n_results=k,
            where=self._build_filter(filter),
            include=["metadatas", "distances"],
        )
        space = (collection.metadata or {}).get("hnsw:space", "cosine")
        ids = (response.get("ids") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]
        hits: list[VectorHit] = []
        for raw_id, distance, metadata in zip(ids, distances, metadatas, strict=True):
            payload = _decode_metadata(metadata)
            hits.append(
                VectorHit(
                    id=str(payload.get(_ID_KEY, raw_id)),
                    score=_score(space, float(distance)),
                    payload=payload,
                )
            )
        return hits

    def _build_filter(  # noqa: ANN202
        self, filter: Mapping[str, Any] | None  # noqa: A002 - matches store vocabulary
    ):
        """Translate a ``{field: value | [values]}`` mapping into a Chroma ``where``.

        ``None`` is encoded to the null sentinel, so an unscoped-tenant filter selects
        exactly the null-tenant points instead of silently dropping the condition (which
        is how a Chroma ``None`` would behave, and how a tenant leak starts).
        """
        if not filter:
            return None
        clauses = []
        for field, value in filter.items():
            if isinstance(value, (list, tuple, set)):
                clause = {"$in": [_encode_value(field, v) for v in value]}
            else:
                clause = {"$eq": _encode_value(field, value)}
            clauses.append({field: clause})
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def close(self) -> None:
        """Close the underlying client (releases an on-disk lock in embedded mode)."""
        self._collections.clear()
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


# --------------------------------------------------------------------------- helpers


def _score(space: str, distance: float) -> float:
    """Convert a Chroma ``distance`` into a similarity (higher is better).

    Chroma reports distances; the pipeline (and the memory composite score) is written
    against similarities. ``cosine`` and ``ip`` distances are both ``1 - similarity``, so
    they invert back exactly — a cosine-identical vector scores ``1.0``. ``l2`` has no
    bounded similarity, so its squared distance is simply negated to keep "higher is
    better" true for ranking.
    """
    if space == "l2":
        return -distance
    return 1.0 - distance


def _split_url(url: str) -> tuple[str, int, bool]:
    """Split a Chroma server URL into ``(host, port, ssl)``."""
    parts = urlsplit(url if "//" in url else f"//{url}")
    ssl = parts.scheme == "https"
    host = parts.hostname or "localhost"
    port = parts.port or (443 if ssl else 8000)
    return host, port, ssl


def _ephemeral_database(chroma: Any, config: Any, settings: Any) -> str:  # noqa: ANN401
    """Create and return a fresh database name inside the shared ephemeral engine.

    Chroma identifies its embedded system by persist directory, so all in-memory clients
    in a process share one engine and (by default) one database. Each ``local()`` store
    therefore gets its own database, which is what makes two stores — and two tests —
    genuinely independent.
    """
    global _ADMIN
    with _ADMIN_LOCK:
        if _ADMIN is None:
            _ADMIN = chroma.AdminClient(settings)
        database = f"aegis-{uuid.uuid4().hex}"
        _ADMIN.create_database(database, config.DEFAULT_TENANT)
    return database


# ─────────────────────────────────────────────────────────────────────────── seam
# The process-wide vector-store choice — configured out loud, never guessed.
#
# Phase 8 §8.4. ``InMemoryKnowledgeBackend`` used to build ``ChromaVectorStore.local()``
# for any caller that handed it none, which meant a host that simply forgot to wire a
# durable store got a *working-looking* system whose vectors evaporated on restart, with
# no log line and no error to follow. That is the one failure mode a first-attempt
# integration cannot recover from, so the default is gone: a component that needs a store
# and was given none now asks here, and this raises unless the process said which engine
# it wants.
#
# A **factory**, not a store instance, on purpose: every backend must own its own Chroma
# database (see :func:`_ephemeral_database`), so handing them one shared instance would
# silently merge two corpora into one collection namespace. The factory is called once
# per component that needs a store.
_STORE_FACTORY: Callable[[], ChromaVectorStore] | None = None


class VectorStoreNotConfiguredError(RuntimeError):
    """No vector store was configured, and the component that needs one will not guess.

    Raised by :func:`new_default_store` — and, through it, by any component built without
    an explicit ``vector_store=``. It is deliberately a hard failure: the alternative
    (an ephemeral in-process index) is indistinguishable from a working deployment until
    the first restart loses every vector.
    """


def configure_vector_store(factory: Callable[[], ChromaVectorStore] | None) -> None:
    """Declare which vector store components build when they are handed none.

    Call once at host startup, next to the other ``configure_*`` seams. The two honest
    choices are both explicit::

        configure_vector_store(lambda: ChromaVectorStore.local(path=settings.path))  # durable
        configure_vector_store(ChromaVectorStore.local)                              # ephemeral

    Args:
        factory: A zero-argument callable returning a fresh
            :class:`ChromaVectorStore`; ``None`` clears the declaration (the next
            component that needs a store raises :class:`VectorStoreNotConfiguredError`).
    """
    global _STORE_FACTORY  # noqa: PLW0603 - a single deliberate configuration seam
    _STORE_FACTORY = factory


def reset_vector_store() -> None:
    """Clear the configured factory (test teardown; the next use raises again)."""
    configure_vector_store(None)


def new_default_store() -> ChromaVectorStore:
    """Build a fresh store from the configured factory, or fail loud.

    Returns:
        A new :class:`ChromaVectorStore` from the factory given to
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
            "aegis.retrieval.configure_vector_store(lambda: ChromaVectorStore.local("
            "path=...)) at startup for a DURABLE on-disk store, or "
            "configure_vector_store(ChromaVectorStore.local) to choose the EPHEMERAL "
            "in-process engine explicitly (dev/tests/offline evals) — or pass "
            "vector_store=... to this component. It used to build the ephemeral store "
            "for you, which made a forgotten call look exactly like a working system "
            "until a restart lost every vector."
        )
    return _STORE_FACTORY()
