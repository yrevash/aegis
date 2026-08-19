"""The knowledge graph is real: typed-entity extraction, caching, and a merged KG.

These tests pin the honesty guarantees of the offline graph: entities come from real
text, the LLM extractor caches to disk (offline replay, no re-call), entities merge
across chunks into one node, and the graph slice is the touched-entity subgraph with no
synthetic document chain.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aegis.core.models import ModelRole
from aegis.retrieval.graph_extract import (
    ENTITY_TYPES,
    Entity,
    LLMCachedExtractor,
    SpacyExtractor,
    build_extractor,
    find_mentions,
)
from aegis.retrieval.memory import InMemoryKnowledgeBackend
from aegis.retrieval.models import Candidate, Chunk
from aegis.retrieval.types import RetrievalScope


class FakeGraphComplete:
    """A completer that returns a graph JSON for whatever entities appear in a passage.

    It mimics a real extraction LLM closely enough to exercise parsing, caching and
    merging: it only reports entities/relations whose surface forms are literally in the
    passage, and it counts its own calls so cache-hit behaviour is verifiable.
    """

    ENTITIES = {
        "Acme Corp": "organization",
        "Widget": "product",
        "London": "location",
        "John Doe": "person",
        "closure policy": "policy",
    }
    RELATIONS = [
        ("Acme Corp", "Widget", "launched"),
        ("Acme Corp", "London", "based in"),
        ("John Doe", "Acme Corp", "manages"),
        ("John Doe", "Widget", "handled"),
    ]

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, role, messages, *, temperature=0.0, response_format=None):
        self.calls += 1
        passage = str(messages[-1]["content"])
        ents = [
            {"name": name, "type": kind}
            for name, kind in self.ENTITIES.items()
            if name in passage
        ]
        present = {e["name"] for e in ents}
        rels = [
            {"source": s, "target": t, "relation": r}
            for s, t, r in self.RELATIONS
            if s in present and t in present
        ]
        return SimpleNamespace(content=json.dumps({"entities": ents, "relations": rels}))


# A small corpus where entities recur across documents (so merging is observable).
_KG_DOCS = [
    ("docA", "Acme Corp launched Widget. Acme Corp is based in London."),
    ("docB", "John Doe manages Acme Corp. Acme Corp published a closure policy."),
    ("docC", "Widget had an outage. John Doe handled Widget."),
]


# ─────────────────────────────────────────────────────────────────────────────
# Extractor unit tests
# ─────────────────────────────────────────────────────────────────────────────


async def test_spacy_extractor_yields_typed_entities_and_relations():
    extractor = build_extractor()  # no completer → deterministic spaCy (or no-op)
    if not isinstance(extractor, SpacyExtractor):
        pytest.skip("spaCy en_core_web_sm not installed in this environment")

    entities, relations = await extractor.extract(
        "Acme Corp fired John Smith in London last year."
    )
    kinds = {e.kind for e in entities}
    assert {"organization", "person"} <= kinds
    assert all(e.kind in ENTITY_TYPES for e in entities)  # vocabulary-clean
    # Entities sharing the sentence are related by a real phrase, never a constant blank.
    assert relations and all(r.phrase for r in relations)


async def test_llm_cached_extractor_parses_caches_and_replays_offline(tmp_path):
    complete = FakeGraphComplete()
    extractor = LLMCachedExtractor(complete, tmp_path, role=ModelRole.CHEAP)
    text = "Acme Corp launched Widget in London."

    entities, relations = await extractor.extract(text)
    labels = {e.label for e in entities}
    assert {"Acme Corp", "Widget", "London"} <= labels
    assert all(e.kind in ENTITY_TYPES for e in entities)
    assert any(r.phrase == "launched" for r in relations)  # real phrase, not a constant
    assert complete.calls == 1

    # Second call for identical text is served from the disk cache: NO second LLM call.
    again_e, again_r = await extractor.extract(text)
    assert complete.calls == 1
    assert {e.id for e in again_e} == {e.id for e in entities}
    assert {(r.src_id, r.tgt_id, r.phrase) for r in again_r} == {
        (r.src_id, r.tgt_id, r.phrase) for r in relations
    }

    # A fresh extractor with NO completer replays the same result from cache (offline).
    replay = LLMCachedExtractor(None, tmp_path)
    replay_e, _ = await replay.extract(text)
    assert {e.id for e in replay_e} == {e.id for e in entities}


async def test_llm_extractor_fails_soft_on_bad_output(tmp_path):
    class Broken:
        async def __call__(self, role, messages, *, temperature=0.0, response_format=None):
            return SimpleNamespace(content="not json at all {{{")

    extractor = LLMCachedExtractor(Broken(), tmp_path)
    entities, relations = await extractor.extract("Some passage.")
    assert entities == [] and relations == []  # never crashes, honest empty


def test_build_extractor_prefers_llm_when_completer_present(tmp_path):
    llm = build_extractor(complete=FakeGraphComplete(), working_dir=tmp_path, prefer="llm")
    assert llm.name == "llm-cached"
    fallback = build_extractor(working_dir=tmp_path)  # no completer
    assert fallback.name in {"spacy", "noop"}


def test_entity_id_merges_by_normalized_label():
    a = Entity.make("Acme Corp", "organization")
    b = Entity.make("  acme   corp ", "ORGANIZATION")
    assert a.id == b.id == "organization:acme corp"


def test_find_mentions_is_literal_and_word_bounded():
    ent = Entity.make("Acme", "organization")
    assert find_mentions("We use Acme daily.", [ent]) == {ent.id}
    assert find_mentions("AcmeCorp is different.", [ent]) == set()  # no false substring


# ─────────────────────────────────────────────────────────────────────────────
# Backend knowledge-graph tests
# ─────────────────────────────────────────────────────────────────────────────


async def test_backend_merges_entities_across_chunks(tmp_path):
    extractor = LLMCachedExtractor(FakeGraphComplete(), tmp_path)
    backend = InMemoryKnowledgeBackend.from_corpus(docs=_KG_DOCS, extractor=extractor)

    new_entities, new_relations = await backend._ensure_extracted()
    assert new_entities > 0 and new_relations > 0

    # "Acme Corp" appears in docA and docB but is ONE node linking both chunks.
    acme_id = "organization:acme corp"
    assert acme_id in backend.entities
    linked_chunks = backend.entity_chunks[acme_id]
    assert len({backend_doc(backend, cid) for cid in linked_chunks}) >= 2

    # Relations are real typed edges, never the retired constant "related".
    assert backend.relations
    assert all(r.phrase and r.phrase != "related" for r in backend.relations)


async def test_ingest_chunks_returns_real_counts_and_is_offline_on_reingest(tmp_path):
    complete = FakeGraphComplete()
    backend = InMemoryKnowledgeBackend([], extractor=LLMCachedExtractor(complete, tmp_path))
    chunks = [
        Chunk(id="c0", doc_id="d", ordinal=0, text="Acme Corp launched Widget."),
        Chunk(id="c1", doc_id="d", ordinal=1, text="John Doe manages Acme Corp."),
    ]

    entities, relations = await backend.ingest_chunks(chunks)
    assert entities is not None and relations is not None  # never None in lite now
    assert entities > 0
    calls_after_first = complete.calls

    # Re-ingesting the same chunks adds nothing and makes no further LLM calls.
    again = await backend.ingest_chunks(chunks)
    assert again == (0, 0)
    assert complete.calls == calls_after_first


async def test_graph_slice_is_touched_entity_subgraph_no_chain(tmp_path):
    extractor = LLMCachedExtractor(FakeGraphComplete(), tmp_path)
    backend = InMemoryKnowledgeBackend.from_corpus(docs=_KG_DOCS, extractor=extractor)
    await backend._ensure_extracted()

    doc_c_chunk = next(c for c in backend._chunks if c.doc_id == "docC")
    candidate = Candidate(id=doc_c_chunk.id, text=doc_c_chunk.text)
    # The unscoped corpus: these chunks record no owner, so the shared-corpus scope is
    # the one that may read them (a null tenant is not a wildcard — see RetrievalScope).
    nodes, edges = backend._graph_slice([candidate], RetrievalScope(tenant_id=None))

    touched = {n.id for n in nodes}
    # Exactly the entities docC mentions — Widget and John Doe, not Acme/London.
    assert touched == backend.mentions[doc_c_chunk.id]
    assert "location:london" not in touched
    assert "organization:acme corp" not in touched
    assert all(n.kind in ENTITY_TYPES for n in nodes)  # typed entities, never "source"

    # Every edge is a real relation with a real phrase between two touched nodes.
    for edge in edges:
        assert edge.source in touched and edge.target in touched
        assert edge.relation and edge.relation != "related"
    assert any(e.relation == "handled" for e in edges)  # the real John Doe→Widget edge


def backend_doc(backend: InMemoryKnowledgeBackend, chunk_id: str) -> str:
    """Return the doc id owning ``chunk_id`` (helper for the merge assertion)."""
    return next(c.doc_id for c in backend._chunks if c.id == chunk_id)
