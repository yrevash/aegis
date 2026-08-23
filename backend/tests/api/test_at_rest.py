"""The at-rest posture reports what is true, and cannot be talked into reporting more.

Why this file exists. A compliance audit found nothing encrypted at rest. The remedy
chosen was **not** column-level encryption — the reasoning is in
``app/platform/at_rest.py``: one document comes to rest in eight places, column
encryption reaches three of them, and the two holding the most content (``chunks.text``,
``memory_fact``) cannot be encrypted without removing retrieval and semantic recall. The
remedy is transparent volume encryption, which is a deployment control.

That leaves code with exactly one job, and it is the job a surface like this usually
fails at: **not claiming the control**. So what is pinned here is that the report cannot
be more optimistic than the configuration, that a declaration is visibly a declaration
rather than a measurement, and that the one fact Aegis genuinely measures is measured.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from app.config import Settings
from app.platform.at_rest import (
    Basis,
    EncryptionState,
    StorageEncryption,
    at_rest_summary,
    build_at_rest,
)


def _settings(**kwargs: object) -> Settings:
    return Settings(**kwargs)  # type: ignore[arg-type]


def test_the_default_posture_is_none_not_unknown():
    """A blank that means "probably fine" is the failure this surface exists to refuse.

    An operator who has configured nothing must read ``none``, and the count of
    unprotected stores must be non-zero — not an empty table that a reviewer skims past.
    """
    report = build_at_rest(_settings())
    assert report.declared_control is StorageEncryption.NONE
    assert report.unencrypted > 0
    assert report.covered == 0
    assert all(
        s.encryption is not EncryptionState.COVERED for s in report.stores
    )


def test_an_unrecognised_declaration_reads_as_none_never_as_a_guess():
    """A typo in an environment variable must not buy a green cell.

    ``AEGIS_STORAGE_ENCRYPTION=volumes`` is the realistic mistake, and the reassuring
    direction is the wrong one to round towards.
    """
    for bad in ("volumes", "yes", "true", "TDE", ""):
        report = build_at_rest(_settings(storage_encryption=bad))
        assert report.declared_control is StorageEncryption.NONE, bad
        assert report.covered == 0, bad


def test_a_declared_control_is_labelled_declared_and_never_measured():
    """The load-bearing honesty property: Aegis cannot see the block device.

    A row that says "covered" must also say the basis was the operator's word, because a
    reviewer's next question is "who checked?" and the answer here is "nobody".
    """
    report = build_at_rest(_settings(storage_encryption="volume"))
    assert report.declared_control is StorageEncryption.VOLUME
    covered = [s for s in report.stores if s.encryption is EncryptionState.COVERED]
    assert covered
    assert all(s.basis is Basis.DECLARED for s in covered)
    assert "cannot verify" in report.note or "cannot verify" in at_rest_summary(
        _settings(storage_encryption="volume")
    )


def test_column_encryption_is_reported_absent_rather_than_omitted():
    """"We chose not to" and "we never got to it" look identical unless one is stated."""
    for declared in ("none", "volume", "provider"):
        report = build_at_rest(_settings(storage_encryption=declared))
        assert report.column_encryption is False


def test_a_store_this_deployment_does_not_use_is_not_counted_as_a_gap():
    """A store holding nothing is neither a failure nor a success.

    Counting an unconfigured graph store as "unencrypted" would inflate the gap and
    teach a reader to discount the number, which is how a real gap gets missed.
    """
    report = build_at_rest(_settings(neo4j_uri="", redis_url=""))
    idle = [s for s in report.stores if s.encryption is EncryptionState.NOT_CONFIGURED]
    assert idle
    assert all(s.location == "" for s in idle)
    assert report.unencrypted == sum(
        1 for s in report.stores if s.encryption is EncryptionState.NONE
    )


def test_every_store_names_a_setting_that_actually_exists():
    """The bug this caught, kept so it cannot come back.

    The first draft of the inventory located the vector store at ``vector_store_url``.
    No such field exists — the Qdrant node is ``qdrant_url`` — so ``getattr`` returned
    empty and the row read ``not_configured``: *there is nothing here to protect*, about
    a node holding an embedding of every chunk in the corpus. The most optimistic answer
    available, produced by a typo, on the one surface built to refuse optimistic answers.

    A ``getattr`` default is exactly the shape of mistake that cannot fail loudly, so
    the inventory is checked against ``Settings.model_fields`` here instead.
    """
    from app.platform.at_rest import _DECLARED  # noqa: PLC0415,PLC2701 - under test

    fields = set(Settings.model_fields)
    named = {setting for _id, _n, setting, _h, _note in _DECLARED}
    assert named <= fields, f"at_rest names settings that do not exist: {named - fields}"

    # And the store it got wrong is present and unprotected, not quietly absent.
    vectors = next(
        s for s in build_at_rest(_settings()).stores if s.id == "vector-store"
    )
    assert vectors.setting == "qdrant_url"
    assert vectors.encryption is EncryptionState.NONE
    assert vectors.location


def test_a_dsn_password_never_reaches_the_report():
    """This is rendered in a console and served over an API."""
    report = build_at_rest(
        _settings(postgres_dsn="postgresql://aegis_app:hunter2@db.example:5432/taif")
    )
    assert not any("hunter2" in s.location for s in report.stores)
    postgres = next(s for s in report.stores if s.id == "postgres")
    assert "db.example:5432/taif" in postgres.location


def test_the_document_store_file_mode_is_measured_not_asserted(tmp_path: Path):
    """The one fact this process can actually check, and the case where it must complain.

    The store's files are 0600 by construction (``tempfile.mkstemp``'s mode, preserved
    through ``os.replace``), which is access control rather than encryption — but a
    written claim of "0600" keeps reassuring after somebody loosens them, and a ``stat``
    does not.
    """
    store = tmp_path / "docs" / "t1" / "ab"
    store.mkdir(parents=True)
    tight = store / "a.bin"
    tight.write_bytes(b"x")
    os.chmod(tight, 0o600)
    note = next(
        s.note
        for s in build_at_rest(_settings(document_store_path=str(tmp_path / "docs"))).stores
        if s.id == "document-store"
    )
    assert "0600" in note and "as intended" in note

    loose = store / "b.bin"
    loose.write_bytes(b"x")
    os.chmod(loose, 0o644)
    note = next(
        s.note
        for s in build_at_rest(_settings(document_store_path=str(tmp_path / "docs"))).stores
        if s.id == "document-store"
    )
    assert "LOOSER THAN 0600" in note
    assert stat.S_IMODE(loose.stat().st_mode) == 0o644


def test_the_boot_summary_names_the_control_and_the_way_to_declare_it():
    """A line an operator scrolls past teaches nothing; this one has to be actionable."""
    unset = at_rest_summary(_settings())
    assert "NONE declared" in unset
    assert "AEGIS_STORAGE_ENCRYPTION" in unset
    assert "volume encryption" in unset

    declared = at_rest_summary(_settings(storage_encryption="provider"))
    assert "provider" in declared
    assert "not verified" in declared
