"""A re-chunk must converge, and a prune must never be able to empty an index.

The dense index is content-addressed, and the module claimed a re-run therefore
"converges rather than growing". That holds only while chunk BOUNDARIES are unchanged.
They changed, ids changed, and the store grew: 50 of 167 points on this deployment (30%)
were orphans from a superseded ingest — three documents each carrying two complete
independent chunkings.

Orphans are not inert. Two windows over the same §1026.13(c)(1) sentence were retrieved
together and cited as `[source 1]` and `[source 4]`, and the answer described them as two
agents confirming one another. One passage, two source numbers, presented as
corroboration.
"""

from __future__ import annotations

import pytest

from aegis.retrieval.chunk_index import prune_stale_chunk_points


class _FakeQdrant:
    """Enough Qdrant to exercise the scope read and the delete."""

    def __init__(self, stored: list[tuple[str, str]]) -> None:
        # (point id, file_path)
        self._stored = stored
        self.deleted: list[str] = []

    def collection_exists(self, _collection: str) -> bool:
        return True

    def upsert(self, **_kwargs: object) -> None:
        """Never called here — `_require_client` insists it exists before any read."""
        raise AssertionError("prune must not write points")

    def scroll(self, **kwargs: object) -> tuple[list[object], None]:
        class _Rec:
            def __init__(self, pid: str, path: str) -> None:
                self.id = pid
                self.payload = {"file_path": path, "workspace_id": "_"}

        return [_Rec(p, f) for p, f in self._stored], None

    def delete(self, *, collection_name: str, points_selector: list[str]) -> None:  # noqa: ARG002
        self.deleted.extend(points_selector)


def test_a_superseded_chunking_is_removed() -> None:
    """The orphans go; the current chunking stays."""
    client = _FakeQdrant(
        [("new-1", "t1::doc.pdf"), ("new-2", "t1::doc.pdf"), ("old-1", "t1::doc.pdf")]
    )
    stale = prune_stale_chunk_points(client, ["new-1", "new-2"], workspace="_")

    assert stale == frozenset({"old-1"})
    assert client.deleted == ["old-1"]


def test_dry_run_reports_without_deleting() -> None:
    """A destructive operation needs a way to be inspected before it runs."""
    client = _FakeQdrant([("new-1", "t1::doc.pdf"), ("old-1", "t1::doc.pdf")])
    stale = prune_stale_chunk_points(client, ["new-1"], workspace="_", dry_run=True)

    assert stale == frozenset({"old-1"})
    assert client.deleted == [], "dry_run deleted anyway"


def test_an_empty_expected_set_is_refused() -> None:
    """The guard that matters more than the feature.

    A scope that legitimately holds nothing and a scope whose chunks failed to load look
    identical here, and the second reading would delete the tenant's entire index. There
    is no safe default, so it raises instead of choosing one.
    """
    client = _FakeQdrant([("a", "t1::doc.pdf"), ("b", "t1::doc.pdf")])

    with pytest.raises(ValueError, match="empty expected set"):
        prune_stale_chunk_points(client, [], workspace="_")

    assert client.deleted == [], "it deleted before raising"


def test_nothing_is_deleted_when_the_index_already_matches() -> None:
    """The converged case must be a no-op, not a delete-and-rewrite."""
    client = _FakeQdrant([("a", "t1::doc.pdf"), ("b", "t1::doc.pdf")])
    assert prune_stale_chunk_points(client, ["a", "b"], workspace="_") == frozenset()
    assert client.deleted == []
