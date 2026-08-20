"""LightRAG backend: entity/relation counts are MEASURED from the graph, not faked.

These tests pin the honesty fix for the graph-ingest count: ``ingest_chunks`` must
report the real ``(entities, relations)`` this ingest added to LightRAG's knowledge
graph (the node/edge delta read back from the graph store), and must NOT return a
hardcoded ``(0, 0)``. LightRAG itself is never imported: we inject a fake ``rag`` into
the backend (the same ``_rag`` cache ``_ensure`` fills), which is the real seam the
production path uses.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

import pytest

from aegis.retrieval.lightrag_backend import LightRAGBackend, _to_recall
from aegis.retrieval.models import Chunk
from aegis.retrieval.pipeline import RetrievalConfig, Retriever
from aegis.retrieval.types import TENANT_METADATA_KEY, RetrievalScope

from .conftest import FakeRedis, RecordingComplete, SequenceEmbed

#: The unscoped (no tenant) partition these tests run under.
_SCOPE = RetrievalScope(tenant_id=None)


class FakeGraphStore:
    """Stand-in for LightRAG's ``chunk_entity_relation_graph`` store.

    Exposes ``get_knowledge_graph`` exactly like LightRAG's ``BaseGraphStorage`` — the
    accessor the backend uses to count the live graph — over in-memory node/edge lists.
    """

    def __init__(self) -> None:
        self.nodes: list[str] = []
        self.edges: list[tuple[str, str]] = []

    async def get_knowledge_graph(self, node_label: str, max_nodes: int = 1000) -> object:
        assert node_label == "*"  # backend snapshots the whole graph
        return SimpleNamespace(nodes=list(self.nodes), edges=list(self.edges))


class FakeRag:
    """Fake LightRAG that 'extracts' entities/relations into its graph on insert.

    Each inserted chunk merges two entities and one relationship, so the graph grows by
    a known, measurable amount — the exact thing ``ingest_chunks`` must read back.
    """

    def __init__(self, *, graph: FakeGraphStore | None = None) -> None:
        self.chunk_entity_relation_graph = graph
        self.inserts: list[dict] = []

    async def ainsert(
        self,
        texts: Sequence[str],
        ids: Sequence[str] | None = None,
        file_paths: Sequence[str] | None = None,
    ) -> None:
        self.inserts.append(
            {
                "texts": list(texts),
                "ids": list(ids or []),
                "file_paths": list(file_paths or []),
            }
        )
        if self.chunk_entity_relation_graph is None:
            return
        graph = self.chunk_entity_relation_graph
        base = len(graph.nodes)
        for i in range(len(texts)):
            a, b = f"e{base + 2 * i}", f"e{base + 2 * i + 1}"
            graph.nodes.extend([a, b])
            graph.edges.append((a, b))


def _backend(rag: FakeRag) -> LightRAGBackend:
    backend = LightRAGBackend(
        complete=RecordingComplete("{}"), embed=SequenceEmbed([1.0, 0.0])
    )
    backend._rag = rag  # inject the fake so _ensure() returns it without importing LightRAG
    return backend


def _chunks(n: int, *, prefix: str = "c") -> list[Chunk]:
    return [
        Chunk(id=f"{prefix}{i}", doc_id="d", ordinal=i, text=f"chunk {prefix} number {i}")
        for i in range(n)
    ]


async def test_ingest_reports_measured_graph_delta_not_hardcoded_zero():
    rag = FakeRag(graph=FakeGraphStore())
    backend = _backend(rag)

    entities, relations = await backend.ingest_chunks(_chunks(2))

    # Two chunks → 4 entities + 2 relations merged into the graph. The count is the
    # REAL delta read from the graph store, never the old hardcoded (0, 0).
    assert (entities, relations) == (4, 2)
    assert (entities, relations) != (0, 0)
    assert rag.inserts, "the chunks were actually inserted"


async def test_ingest_delta_is_per_ingest_not_cumulative():
    rag = FakeRag(graph=FakeGraphStore())
    backend = _backend(rag)

    first = await backend.ingest_chunks(_chunks(2, prefix="a"))
    second = await backend.ingest_chunks(_chunks(3, prefix="b"))

    assert first == (4, 2)  # first ingest's own contribution
    assert second == (6, 3)  # second ingest reports only what IT added (delta, not total)


async def test_ingest_returns_none_when_graph_store_unavailable():
    # No graph store to query → honest "unknown", never a fabricated number.
    rag = FakeRag(graph=None)
    backend = _backend(rag)

    entities, relations = await backend.ingest_chunks(_chunks(2))

    assert (entities, relations) == (None, None)
    assert rag.inserts, "chunks are still inserted even when counts are unknown"


async def test_ingest_empty_is_noop_zero():
    rag = FakeRag(graph=FakeGraphStore())
    backend = _backend(rag)

    assert await backend.ingest_chunks([]) == (0, 0)
    assert not rag.inserts  # nothing inserted for an empty batch


class QueryingRag(FakeRag):
    """A fake LightRAG answering context queries the way LightRAG 1.5.6 answers them.

    Both calls are modelled, because the difference between them *is* the bug this fake
    used to hide. It previously implemented only ``aquery``, and returned a
    ``SimpleNamespace(context=..., raw_data=...)`` from it — a shape LightRAG 1.5.6 never
    returns. Every recall test passed against that invention while production refused
    every tenant-scoped query, because the real ``aquery`` hands back a merged prose blob
    with no per-chunk ``file_path`` in it and the backend can attribute nothing.

    So:

    * :meth:`aquery_data` returns ``convert_to_user_format``'s real mapping and is the
      call the backend must make.
    * :meth:`aquery` returns a plain ``str``, as the library does. Any regression to it
      turns these tests' tenant assertions into the refusal production was suffering.
    """

    def __init__(self, chunks: list[dict]) -> None:
        super().__init__()
        self._chunks = chunks

    async def aquery_data(self, query: str, param: object = None) -> dict:
        return {
            "status": "success",
            "message": "Query processed successfully",
            "data": {
                "entities": [],
                "relationships": [],
                "chunks": self._chunks,
                "references": [],
            },
            "metadata": {"query_mode": getattr(param, "mode", "naive")},
        }

    async def aquery(self, query: str, param: object = None) -> str:
        return "blended context"


async def test_ingest_tags_the_owning_tenant_into_the_stored_file_path():
    """LightRAG round-trips ``file_path`` and nothing else, so the tenant rides in it.

    The shared-corpus chunk is tagged too, and explicitly. It used to be stored with its
    path untouched, which made "belongs to the shared corpus" and "nobody recorded an
    owner" the same bytes — and every scope may read the shared corpus, so an untagged
    row was readable by everyone. See ``_SHARED_TAG``.
    """
    rag = FakeRag(graph=FakeGraphStore())
    backend = _backend(rag)
    await backend.ingest_chunks(
        [
            Chunk(
                id="c0",
                doc_id="d",
                ordinal=0,
                text="t",
                metadata={"source": "handbook.md", "tenant_id": "t7"},
            ),
            Chunk(id="c1", doc_id="d", ordinal=1, text="t", metadata={"source": "public.md"}),
        ]
    )
    assert rag.inserts[0]["file_paths"] == ["t7::handbook.md", "shared::public.md"]


async def test_recall_drops_another_tenants_rows_and_restores_the_clean_source():
    rag = QueryingRag(
        [
            {"id": "a", "content": "acme secret", "file_path": "t1::acme.md"},
            {"id": "b", "content": "globex secret", "file_path": "t2::globex.md"},
            {"id": "s", "content": "shared handbook", "file_path": "shared::handbook.md"},
        ]
    )
    recall = await _backend(rag).recall("q", top_k=5, scope=RetrievalScope(tenant_id=1))

    assert [c.id for c in recall.candidates] == ["a", "s"]
    # The tag is stripped back off, so citations read exactly as they did before.
    assert [c.metadata["file_path"] for c in recall.candidates] == ["acme.md", "handbook.md"]


async def test_a_chunk_with_no_owner_tag_is_refused_by_a_tenant_scoped_recall():
    """An untagged stored path establishes nothing, so it is served to nobody scoped.

    It used to read as the shared corpus — ``_untag_file_path`` returned ``(None, path)``
    for it and ``_candidates_from_payload`` stamped ``tenant_attributed: True`` anyway —
    and every tenant may read the shared corpus. So one row whose tag was lost (a corpus
    loaded into the working directory by hand, a row written before tagging existed, a
    LightRAG version that normalised the path) was visible to *every* tenant at once,
    which is the opposite direction to ``chunks.tenant_id`` being ``NOT NULL``.

    The tenant's own tagged row still comes back, so this is a per-row refusal and not
    the whole request failing.
    """
    rag = QueryingRag(
        [
            {"id": "a", "content": "acme secret", "file_path": "t1::acme.md"},
            {"id": "u", "content": "untagged settlement figure", "file_path": "board.pdf"},
        ]
    )
    recall = await _backend(rag).recall("q", top_k=5, scope=RetrievalScope(tenant_id=1))

    assert [c.id for c in recall.candidates] == ["a"]
    assert not any("settlement" in c.text for c in recall.candidates)


async def test_unscoped_recall_sees_only_shared_rows():
    """A null tenant reads the shared corpus, never every tenant's."""
    rag = QueryingRag(
        [
            {"id": "a", "content": "acme secret", "file_path": "t1::acme.md"},
            {"id": "s", "content": "shared handbook", "file_path": "shared::handbook.md"},
        ]
    )
    recall = await _backend(rag).recall("q", top_k=5, scope=_SCOPE)
    assert [c.id for c in recall.candidates] == ["s"]


class BlendingRag(FakeRag):
    """A LightRAG with **no** ``aquery_data`` — prose context and nothing else.

    The degraded path, and the one the backend used to take unconditionally. LightRAG
    1.5.6's ``aquery(only_need_context=True)`` returns exactly this: one merged,
    prompt-shaped string in which every chunk's text has been concatenated and every
    chunk's ``file_path`` discarded.
    """

    async def aquery(self, query: str, param: object = None) -> str:
        return "blended context"


async def test_unattributable_blended_context_is_refused_for_a_tenant():
    """A row with no per-chunk path cannot be shown to belong to this tenant → fail loud.

    Dropping it silently would hide a store that cannot be scoped; serving it is the
    leak. Only the tenant-scoped case raises — an unscoped run has no boundary to cross.
    """
    backend = _backend(BlendingRag())

    with pytest.raises(RuntimeError, match="unattributable"):
        await backend.recall("q", top_k=5, scope=RetrievalScope(tenant_id=1))

    unscoped = await backend.recall("q", top_k=5, scope=_SCOPE)
    assert [c.id for c in unscoped.candidates] == ["context"]


async def test_recall_reads_the_structured_context_not_the_prose_blend():
    """The backend must ask LightRAG for data, not for prose. This is the live outage.

    Both calls exist on the fake and they disagree on purpose: ``aquery_data`` returns
    one attributable chunk, ``aquery`` returns the blend. A backend that reads the blend
    cannot attribute a single row, so a tenant-scoped recall raises rather than answers —
    which is precisely what production did with 37 correct, tenant-tagged points sitting
    in Qdrant and a 0.72 cosine hit waiting for the query.

    The assertion is therefore that a tenant-scoped recall *succeeds and returns the
    row*, which is unreachable from the string path in either direction: serving the
    blend would leak it, refusing it answers nothing.
    """
    rag = QueryingRag([{"id": "a", "content": "acme passage", "file_path": "t1::acme.md"}])
    recall = await _backend(rag).recall("q", top_k=5, scope=RetrievalScope(tenant_id=1))

    assert [c.id for c in recall.candidates] == ["a"]
    assert recall.candidates[0].metadata["file_path"] == "acme.md"


async def test_an_empty_structured_result_is_an_empty_recall_not_a_blend():
    """Nothing found is nothing returned — never LightRAG's status prose as a passage.

    ``aquery_data``'s own no-results shape is ``{"status": "failure", "data": {}}``. The
    whole-context fallback must not fire for it: there is no context to fall back to, and
    manufacturing a candidate would put "No relevant document chunks found." into the
    answer as if the corpus had said it. An honest empty recall also keeps a genuinely
    empty index distinguishable from an unscopeable one — the first is a miss, the second
    raises.
    """
    rag = QueryingRag([])
    backend = _backend(rag)

    for scope in (RetrievalScope(tenant_id=1), _SCOPE):
        recall = await backend.recall("q", top_k=5, scope=scope)
        assert recall.candidates == []


def test_the_parser_matches_lightrags_own_serialiser():
    """Our payload reader is pinned against the function LightRAG builds the payload with.

    ``aquery_data``'s return is ``lightrag.utils.convert_to_user_format``'s output, and
    that function renames things on the way out — an entity's name arrives as
    ``entity_name``, never ``entity``; a relationship's endpoints as ``src_id``/
    ``tgt_id``. Reading those keys from a hand-written fixture proves only that the
    fixture and the parser agree, which is the mistake that let this whole defect ship:
    the recall tests passed for months against a fake shape LightRAG never returns.

    So the fixture is generated by LightRAG itself. If it renames a key, this fails —
    instead of the graph arm quietly going empty and the tenant filter quietly losing the
    ``file_path`` it scopes on.
    """
    from lightrag.utils import convert_to_user_format

    payload = convert_to_user_format(
        [{"entity": "Acme Ltd", "type": "organization", "file_path": "t1::acme.md"}],
        [
            {
                "entity1": "Acme Ltd",
                "entity2": "Globex",
                "description": "Acme supplies Globex.",
                "file_path": "t1::acme.md",
            }
        ],
        [{"reference_id": "1", "content": "acme passage", "file_path": "t1::acme.md"}],
        [{"reference_id": "1", "file_path": "t1::acme.md"}],
        "mix",
    )

    recall = _to_recall(payload)

    assert [c.text for c in recall.candidates] == ["acme passage"]
    assert recall.candidates[0].metadata["file_path"] == "acme.md"
    assert recall.candidates[0].metadata[TENANT_METADATA_KEY] == "t1"
    assert [n.label for n in recall.nodes] == ["Acme Ltd"]
    assert [n.kind for n in recall.nodes] == ["organization"]
    assert [(e.source, e.target) for e in recall.edges] == [("Acme Ltd", "Globex")]
    assert recall.edges[0].relation == "Acme supplies Globex."
    # And the provenance the scope filter reads, on both halves of the graph.
    assert recall.nodes[0].owners == ("t1",)
    assert recall.edges[0].owners == ("t1",)


def test_the_context_query_asks_for_data_and_lightrag_still_offers_it():
    """``aquery_data`` exists on the real class, and ``aquery`` still returns prose.

    The two halves of the reason the backend calls one and not the other. A LightRAG
    upgrade that removed ``aquery_data`` would drop every tenant-scoped recall back to
    the refusal path, and it would do so silently — the fallback logs a warning and
    nothing else fails until a real query runs.
    """
    import inspect

    from lightrag import LightRAG

    assert inspect.iscoroutinefunction(LightRAG.aquery_data)
    # ``aquery`` is annotated ``str | AsyncIterator[str]``: prose either way, and no
    # per-chunk ``file_path`` anywhere in it.
    assert "str" in str(inspect.signature(LightRAG.aquery).return_annotation)


async def test_ingest_report_carries_real_counts_through_pipeline():
    # End-to-end: the measured counts reach IngestReport instead of a hardcoded zero.
    from aegis.retrieval.cache import SemanticCache

    rag = FakeRag(graph=FakeGraphStore())
    backend = _backend(rag)
    retriever = Retriever(
        backend=backend,
        cache=SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=0.95),
        complete=RecordingComplete("{}"),
        embed=SequenceEmbed([1.0, 0.0]),
        config=RetrievalConfig(),
    )

    report = await retriever.ingest(
        [{"id": "kb", "text": "The Amazon river discharges more water than any other river."}],
        scope=_SCOPE,
    )

    assert report.chunks_written >= 1
    # Real, measured extraction counts flow into the report (not the old fake 0/0).
    assert report.entities is not None and report.entities > 0
    assert report.relations is not None and report.relations > 0
    assert report.entities == 2 * report.chunks_written
    assert report.relations == report.chunks_written


# ─────────────────────────────────────────── the storage selection and its seams (§9.1)


async def test_the_embedding_adapter_gives_lightrag_what_it_actually_validates():
    """REGRESSION: LightRAG validates ``result.size``, so a list crashes every ingest.

    ``EmbeddingFunc.__call__`` (lightrag 1.5.6) reads ``result.size`` off whatever the
    wrapped callable returns, and the vector storages then ``np.concatenate`` it — but
    :class:`~aegis.retrieval.protocols.EmbedFn` returns ``list[list[float]]``. Handing it
    over unwrapped raised ``AttributeError: 'list' object has no attribute 'size'`` on the
    *first* embed of any ingest. Not a Qdrant-specific defect: NanoVectorDB called the
    same wrapper, so this path was broken before §9.1 selected a different storage.
    """
    from lightrag.utils import EmbeddingFunc

    from aegis.retrieval.lightrag_backend import lightrag_embedding_adapter

    async def embed(texts: Sequence[str]) -> list[list[float]]:
        return [[0.5, 0.5, 0.5] for _ in texts]

    wrapped = EmbeddingFunc(
        embedding_dim=3, max_token_size=8, func=lightrag_embedding_adapter(embed)
    )
    # ``context=`` is what the storages pass; the priority limiter adds ``_priority``.
    result = await wrapped(["a", "b"], context="document")
    assert result.size == 6  # the attribute LightRAG reads, on the object it gets back


def test_vectors_are_configured_onto_qdrant_and_kv_stays_on_postgres(monkeypatch):
    """§9.1: the LightRAG instance must ask for Qdrant, not the JSON-backed default.

    Pinned because ``vector_storage`` is a *string*: a typo or a revert reads as working
    configuration and silently restores NanoVectorDB — a brute-force in-memory scan
    persisted by rewriting a whole JSON file, and single-writer, which is the ceiling this
    task removed.
    """
    import lightrag as lightrag_pkg

    from aegis.retrieval import lightrag_backend as module

    captured: dict[str, object] = {}

    class _FakeRAG:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def initialize_storages(self) -> None: ...

    async def _noop_pipeline_status() -> None: ...

    monkeypatch.setattr(lightrag_pkg, "LightRAG", _FakeRAG)
    monkeypatch.setattr(
        "lightrag.kg.shared_storage.initialize_pipeline_status", _noop_pipeline_status
    )
    monkeypatch.setenv("QDRANT_URL", "")  # cleared, so the config value is what lands
    monkeypatch.delenv("QDRANT_URL", raising=False)

    backend = module.LightRAGBackend(
        RecordingComplete("unused"),
        SequenceEmbed([[1.0, 0.0]]),
        config=RetrievalConfig(qdrant_url="http://qdrant.test:6333"),
    )
    import asyncio

    asyncio.run(backend._ensure())

    assert captured["vector_storage"] == "QdrantVectorDBStorage"
    assert captured["kv_storage"] == "PGKVStorage"  # KV is off files, on Postgres
    # The URL reaches LightRAG the only way its storages read it: the environment.
    import os

    assert os.environ["QDRANT_URL"] == "http://qdrant.test:6333"
