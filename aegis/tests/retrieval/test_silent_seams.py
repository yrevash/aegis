"""The two seams that used to degrade silently to an ephemeral vector store (§8.4).

Both of these once answered a *missing* configuration call with a working-looking
object: ``aegis.memory``'s recall built an in-process Qdrant index on first use, and a
retrieval backend built one in its constructor. An integration that skipped the wiring
therefore passed every smoke test it had, indexed everything into RAM, and lost it on
restart — with no error, no log line and nothing to search for.

These tests pin the replacement behaviour: **skipping the call raises, and the message
names the call to make**. They also pin the two things that must NOT have changed —
passing a store explicitly still works, and each component still gets its *own* store
rather than a shared one (two corpora in one collection namespace would be a different
silent corruption).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from aegis.memory import (
    MemoryVectorIndex,
    VectorStoreNotConfiguredError,
    get_default_index,
    reset_default_index,
    set_default_index,
)
from aegis.memory.vector_ops import topk_by_cosine
from aegis.retrieval import QdrantVectorStore, configure_vector_store
from aegis.retrieval.memory import InMemoryKnowledgeBackend


@pytest.fixture
def unconfigured() -> Iterator[None]:
    """Run one test in a process that never declared a vector store, then restore.

    The session fixture in ``tests/conftest.py`` declares the ephemeral engine for the
    whole suite (as a host does at startup); this puts one test back into the state a
    fresh integrator's process is in.
    """
    configure_vector_store(None)
    reset_default_index()
    try:
        yield
    finally:
        configure_vector_store(QdrantVectorStore.local)
        set_default_index(MemoryVectorIndex.local())


def test_retrieval_backend_refuses_to_invent_a_store(unconfigured: None) -> None:
    """Building a backend with no store and no declaration raises, naming the fix."""
    with pytest.raises(VectorStoreNotConfiguredError) as excinfo:
        InMemoryKnowledgeBackend([])

    message = str(excinfo.value)
    assert "configure_vector_store" in message
    # Both honest choices are in the message: a remedy that names only one of them
    # pushes every reader onto that one.
    assert "url=" in message and "QdrantVectorStore.local" in message
    assert "vector_store=" in message


def test_an_explicit_store_still_works_with_nothing_declared(unconfigured: None) -> None:
    """The escape hatch is untouched: a caller that passes a store never consults the seam."""
    backend = InMemoryKnowledgeBackend([], vector_store=QdrantVectorStore.local())

    assert backend._vector_store.mode == "local"


def test_each_backend_gets_its_own_store_from_the_factory() -> None:
    """The seam holds a factory, not a store: two backends must not share one namespace.

    A shared instance would put two independently-built corpora into the same collection
    prefix — recall would then return another corpus's chunks. This is the reason the
    declaration is ``configure_vector_store(callable)`` and not ``set_vector_store(store)``.
    """
    built: list[QdrantVectorStore] = []

    def factory() -> QdrantVectorStore:
        store = QdrantVectorStore.local()
        built.append(store)
        return store

    configure_vector_store(factory)
    try:
        first = InMemoryKnowledgeBackend([])
        second = InMemoryKnowledgeBackend([])
    finally:
        configure_vector_store(QdrantVectorStore.local)

    assert len(built) == 2
    assert first._vector_store is not second._vector_store


def test_memory_index_refuses_to_conjure_itself(unconfigured: None) -> None:
    """An unconfigured memory index raises, naming ``set_default_index`` and both modes."""
    with pytest.raises(VectorStoreNotConfiguredError) as excinfo:
        get_default_index()

    message = str(excinfo.value)
    assert "set_default_index" in message
    assert "url=" in message and "MemoryVectorIndex.local()" in message


async def test_memory_recall_fails_loud_rather_than_recalling_nothing(
    unconfigured: None,
) -> None:
    """The end-to-end failure mode: recall raises instead of quietly searching nowhere.

    ``topk_by_cosine`` is the call every memory recall path funnels through. Before
    §8.4 this line silently built a private RAM index and returned ``[]`` — a subject
    with a full memory store looked exactly like a subject with none.
    """
    with pytest.raises(VectorStoreNotConfiguredError):
        await topk_by_cosine(
            None,  # type: ignore[arg-type] - raises before the session is touched
            None,
            subject_id="s1",
            query_vec=[0.1, 0.2, 0.3],
            k=3,
        )
