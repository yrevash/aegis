"""The document store's boundaries, asserted rather than described.

``store.py``'s module docstring makes three load-bearing promises — identical bytes write
one path, a path cannot be forged from a filename, and **"tenants are separate
subtrees"** — and until this file there was no ``DocumentStore`` test anywhere in the
repository. The third promise was the one with nothing behind it: making
``_tenant_dir`` return ``self.root`` for every caller, so two tenants share one subtree
and one tenant's delete is the other's data loss, left 59 tests passing.

The same method was also the *unchecked* half of an address whose other half is checked.
``_checked_sha`` refuses a digest that is not 64 hex characters because it becomes a path
segment; ``f"t{tenant_id}"`` interpolated whatever it was handed into the segment beside
it. A ``tenant_id`` reaching here as a string is not hypothetical —
``decode_access_token`` read the JWT claim straight through with no coercion — so both
ends are closed now, and both ends are tested.
"""

from __future__ import annotations

import pytest

from app.ingestion.store import DocumentStore, sha256_of

_A = 4001
_B = 4002
_BYTES = b"%PDF-1.7 tenant A's confidential merger terms"
_SHA = sha256_of(_BYTES)


@pytest.fixture
def store(tmp_path) -> DocumentStore:
    return DocumentStore(tmp_path / "documents")


# ─────────────────────────────────────────────────────────────────────────────
# Tenants are separate subtrees
# ─────────────────────────────────────────────────────────────────────────────


def test_two_tenants_storing_identical_bytes_get_two_independent_files(store) -> None:
    """The digest is unique *per tenant*, so the store must partition the same way.

    Two tenants uploading the same public filing are two independent documents with
    independent deletions; sharing one blob would make one tenant's delete the other
    tenant's data loss, and would let a tenant confirm another tenant holds a document
    by uploading a copy of it.
    """
    mine = store.put(tenant_id=_A, sha256=_SHA, data=_BYTES)
    theirs = store.put(tenant_id=_B, sha256=_SHA, data=_BYTES)

    assert mine != theirs, "both tenants' bytes landed on one path"
    assert mine.is_file() and theirs.is_file()
    assert _A != _B and str(_A) in str(mine) and str(_B) in str(theirs)


def test_one_tenants_delete_does_not_take_another_tenants_bytes(store) -> None:
    """The consequence of the separate subtrees, stated as the thing that would break."""
    store.put(tenant_id=_A, sha256=_SHA, data=_BYTES)
    store.put(tenant_id=_B, sha256=_SHA, data=_BYTES)

    store.path_for(tenant_id=_A, sha256=_SHA).unlink()

    assert not store.has(tenant_id=_A, sha256=_SHA)
    assert store.has(tenant_id=_B, sha256=_SHA)
    assert store.read(tenant_id=_B, sha256=_SHA) == _BYTES


def test_a_tenant_cannot_read_another_tenants_document_by_its_digest(store) -> None:
    """The digest is the whole address, so the tenant half has to carry the isolation."""
    store.put(tenant_id=_A, sha256=_SHA, data=_BYTES)

    assert not store.has(tenant_id=_B, sha256=_SHA)
    with pytest.raises(FileNotFoundError):
        store.read(tenant_id=_B, sha256=_SHA)


def test_the_platform_subtree_is_not_shared_with_any_tenant(store) -> None:
    """``tenant_id=None`` is a subtree of its own, not "wherever the last caller was"."""
    store.put(tenant_id=None, sha256=_SHA, data=_BYTES)

    assert store.has(tenant_id=None, sha256=_SHA)
    assert not store.has(tenant_id=_A, sha256=_SHA)


def test_the_parse_artifact_is_partitioned_exactly_like_the_bytes(store) -> None:
    """The artifact holds the document's recovered text, so it is content too."""
    store.put_artifact(tenant_id=_A, sha256=_SHA, payload='{"blocks": ["secret"]}')

    assert store.read_artifact(tenant_id=_A, sha256=_SHA) == '{"blocks": ["secret"]}'
    with pytest.raises(FileNotFoundError):
        store.read_artifact(tenant_id=_B, sha256=_SHA)


# ─────────────────────────────────────────────────────────────────────────────
# Neither half of the address may be an uncoerced caller value
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tenant_id", ["7", "../t4002", "", 7.0, [7], True])
def test_a_tenant_id_that_is_not_an_integer_is_refused_not_interpolated(
    store, tenant_id
) -> None:
    """``../t4002`` is a directory traversal; ``True`` silently addresses tenant 1."""
    with pytest.raises(ValueError, match="not a tenant id"):
        store.path_for(tenant_id=tenant_id, sha256=_SHA)


def test_the_digest_half_is_refused_the_same_way(store) -> None:
    """The control: this half was already checked, and still is."""
    with pytest.raises(ValueError, match="not a SHA-256 digest"):
        store.path_for(tenant_id=_A, sha256="../../etc/passwd")


def test_a_traversing_tenant_id_cannot_escape_the_root(store) -> None:
    """Belt and braces: the refusal above is what keeps this inside ``root``."""
    with pytest.raises(ValueError):
        store.put(tenant_id="../..", sha256=_SHA, data=_BYTES)  # type: ignore[arg-type]
    assert not list(store.root.parent.glob("*.bin"))


# ─────────────────────────────────────────────────────────────────────────────
# The other two module promises, so this file covers the store rather than one method
# ─────────────────────────────────────────────────────────────────────────────


def test_identical_bytes_write_the_same_path_twice(store) -> None:
    """Content addressing: the row's idempotency and the file's are the same fact."""
    first = store.put(tenant_id=_A, sha256=_SHA, data=_BYTES)
    second = store.put(tenant_id=_A, sha256=_SHA, data=_BYTES)
    assert first == second


def test_bytes_that_do_not_hash_to_the_digest_are_refused(store) -> None:
    """A row and a file that disagree about what was stored is the worse failure."""
    with pytest.raises(ValueError, match="they hash to"):
        store.put(tenant_id=_A, sha256=_SHA, data=b"different bytes entirely")


def test_the_uploaded_filename_never_becomes_a_directory_entry(store) -> None:
    """A name is data on the row, not a path — so no upload can be called ``../``."""
    path = store.put(tenant_id=_A, sha256=_SHA, data=_BYTES)
    assert path.name == f"{_SHA}.bin"


def test_opening_bytes_that_were_never_stored_raises_rather_than_yielding(store) -> None:
    """An absent document must not look like an unreadable one to the parse stage."""
    with pytest.raises(FileNotFoundError), store.open_local(tenant_id=_A, sha256=_SHA):
        pass
