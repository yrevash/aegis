"""Encryption at rest — every place tenant content settles, and what actually protects it.

**The finding this answers, and the judgement made about it.** A compliance audit found
nothing encrypted at rest: ``documents``/``chunks``, ``memory_message``/``memory_fact``,
``chat_messages`` and ``audit_log.payload`` all sit in the clear. The obvious remedy is
column-level encryption on those columns. It was assessed and **deliberately not
implemented**, and the reasoning is written down here rather than left as an absence,
because "we chose not to" and "we never got to it" look identical in a repository.

Count where one uploaded PDF comes to rest in this system. The original bytes, in
``document_storage/<tenant>/<sha>.bin``. Its full parsed text, in the ``.parse.json``
beside it. Its chunk text, in ``chunks``. Its embeddings, in the vector store — which
invert well enough that "encrypted the text, shipped the vectors" is not a defence. The
entities and relations extracted from it, in the graph store. Any answer quoting it, in
the Redis semantic cache and in ``chat_messages``/``memory_message``. And all of the
Postgres ones again, in the WAL and in every backup taken from it.

Column-level encryption on the four column groups the audit named reaches **three** of
those. It would also cost the ability to query them — ``chunks.text`` is what retrieval
reads and ``memory_fact`` is what semantic recall searches — so the two that hold the
most content are the two that cannot be encrypted without removing the feature they
exist for. What that buys is a table cell that reads "encrypted" beside a directory of
unencrypted source PDFs. It is worse than nothing, because it is *believed*.

And the threat it is aimed at is narrower than it sounds. Column encryption defends a
**stolen dump** — a backup that walked out, a disk pulled from a decommissioned host,
an unauthenticated snapshot bucket. It does **not** defend a compromised application:
the process that decrypts on read holds the key, so anything that runs code inside it
reads plaintext, which covers the SQL injection, the deserialisation bug and the
credential theft that make up most of the realistic paths in. Against the one threat it
does answer, transparent volume encryption plus encrypted backups answers the same
threat for **all eight** copies, including the WAL and the temp files no column-level
scheme ever reaches, and costs no queryability at all.

So the control is transparent/volume encryption, and that is a deployment control — a
LUKS or FileVault or cloud-provider-managed volume under the data directory and the
document store, and ``--cipher`` on the backup job. Aegis cannot implement it. What
Aegis can do, and what this module is, is refuse to *claim* it: enumerate every store
from the live wiring, report what genuinely protects each, and mark the difference
between what was **measured** and what the operator **declared**. A deployment that has
not set :attr:`~app.config.Settings.storage_encryption` reads ``none`` here, and reads
``none`` on the compliance surface, until it is actually configured.

The discipline is :mod:`app.platform.residency`'s, for the same reason: a hand-written
answer to "is it encrypted?" is true on the day it is typed and false the first time
somebody re-points a volume. The one thing here that *is* measured is the document
store's file mode, because it is on this filesystem and a ``stat`` is the whole cost.

Nothing here reads a database or dials anything.
"""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import Settings, get_settings

__all__ = [
    "AtRestReport",
    "AtRestStore",
    "Basis",
    "EncryptionState",
    "StorageEncryption",
    "build_at_rest",
]


class StorageEncryption(StrEnum):
    """What the **operator** says protects this deployment's volumes at rest.

    A declaration, never a measurement: Aegis runs above the filesystem and cannot see
    whether the block device under it is encrypted. The value is reported as the
    operator's statement and labelled as such, which is the only honest way to carry a
    fact this process cannot check.
    """

    #: The default, and deliberately so. An unset control reads as absent rather than
    #: as unknown — a compliance surface whose blank means "probably fine" is the exact
    #: failure this module exists to refuse.
    NONE = "none"
    #: Full-disk / volume encryption under the data directory and the document store
    #: (LUKS, FileVault, BitLocker).
    VOLUME = "volume"
    #: A managed service's own at-rest encryption (RDS/Cloud SQL storage encryption,
    #: an encrypted EBS volume, a provider-managed KMS key).
    PROVIDER = "provider"


class EncryptionState(StrEnum):
    """Whether one store's contents are protected at rest."""

    #: Nothing encrypts this store's contents.
    NONE = "none"
    #: Covered by the operator's declared volume/provider encryption.
    COVERED = "covered"
    #: This store is not in use in this deployment, so it holds nothing to protect.
    NOT_CONFIGURED = "not_configured"


class Basis(StrEnum):
    """How this row's verdict was arrived at. The column a reviewer should read second."""

    #: Aegis looked. Only ever used where looking is possible from this process.
    MEASURED = "measured"
    #: The operator declared it and Aegis cannot verify it from here.
    DECLARED = "declared"


class AtRestStore(BaseModel):
    """One place tenant content settles, and what protects it there."""

    id: str = Field(description="Stable slug for the store.")
    name: str = Field(description="Human name.")
    holds: str = Field(description="What tenant content lands here. One sentence.")
    location: str = Field(
        default="", description="Path or host as configured. Empty when not in use."
    )
    setting: str = Field(description="The Settings field that decides where it lives.")
    encryption: EncryptionState = Field(description="none / covered / not_configured.")
    basis: Basis = Field(description="measured / declared.")
    note: str = Field(description="What is and is not true of this store specifically.")


class AtRestReport(BaseModel):
    """The at-rest posture of every store, and the sentence that bounds the claim."""

    generated_at: str = Field(description="ISO-8601 UTC timestamp of this read.")
    declared_control: StorageEncryption = Field(
        description="What the operator declared protects this deployment's volumes."
    )
    stores: list[AtRestStore] = Field(description="One entry per place content rests.")
    unencrypted: int = Field(description="Stores in use with nothing protecting them.")
    covered: int = Field(description="Stores in use covered by the declared control.")
    column_encryption: bool = Field(
        default=False,
        description=(
            "Whether any column-level encryption is in force. Always false, and "
            "reported rather than omitted: see the module docstring for why that is a "
            "decision and not an oversight."
        ),
    )
    note: str = Field(description="What this report can and cannot establish.")


NOTE = (
    "Derived from live configuration on every read, not asserted. 'declared' means the "
    "operator stated it (AEGIS_STORAGE_ENCRYPTION) and this process cannot verify it — "
    "Aegis runs above the filesystem and cannot see whether the block device under it is "
    "encrypted. No column-level encryption is in force anywhere, by decision: it would "
    "reach three of the eight places one document comes to rest, would remove the "
    "queryability that chunks.text and memory_fact exist for, and defends only a stolen "
    "dump — the same threat volume encryption answers for all eight at no cost in "
    "queryability. It defends nothing against a compromised application process, which "
    "holds the key by construction."
)

#: One row per place tenant content comes to rest, as ``(id, name, setting, holds,
#: note)``. Volume-level encryption, if the operator has it, covers every one of these
#: that lives on a volume they control — which is the point: the control is per-volume,
#: so the table is per-store and the verdict is shared.
#:
#: Each ``setting`` was checked against ``Settings.model_fields`` rather than guessed,
#: because the first draft of this table named ``vector_store_url`` — a field that does
#: not exist — and the row therefore read ``not_configured``: *there is nothing here to
#: protect*, about a Qdrant node holding an embedding of every chunk in the corpus. The
#: most optimistic possible answer, produced by a typo, on the one surface built to
#: refuse optimistic answers. ``backend/tests/api/test_at_rest.py`` now asserts every
#: setting named here is a real field.
_DECLARED: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "document-store",
        "Document store (filesystem)",
        "document_store_path",
        "The uploaded PDF's original bytes and its full parsed text, per tenant.",
        "The completest copy of a document in the system. Its files are written 0600 by "
        "app.ingestion.store._atomic_write (tempfile.mkstemp's mode, preserved through "
        "os.replace), so another local user cannot read them — which is access control, "
        "not encryption, and does nothing for a disk or a backup that leaves the host.",
    ),
    (
        "postgres",
        "PostgreSQL",
        "postgres_dsn",
        "documents, chunks.text, memory_message, memory_fact, chat_messages and "
        "audit_log.payload — plus every one of them again in the WAL.",
        "The four column groups the audit named live here. Column-level encryption was "
        "assessed and refused: chunks.text is what retrieval reads and memory_fact is "
        "what semantic recall searches, so encrypting the two that hold the most content "
        "removes the feature they exist for.",
    ),
    (
        "vector-store",
        "Vector store",
        "qdrant_url",
        "Embeddings of every chunk, and the chunk text carried alongside them as payload.",
        "Embeddings are not a redaction: they invert well enough that encrypting the "
        "text column while shipping the vectors protects nothing. This is the node both "
        "aegis.retrieval and LightRAG's QdrantVectorDBStorage write to; "
        "``vector_store_path`` is deliberately NOT listed as a store, because LightRAG's "
        "working directory holds no vectors and no KV any more (see app/config.py).",
    ),
    (
        "graph-store",
        "Knowledge graph",
        "neo4j_uri",
        "Entities, relations and descriptions extracted verbatim from tenant documents.",
        "Outside PostgreSQL entirely, so no database-level control applied there reaches "
        "it.",
    ),
    (
        "cache",
        "Semantic cache",
        "redis_url",
        "Prompts and model answers, which quote the corpus they were grounded on.",
        "Bounded by its TTL rather than by a retention policy, but present in a memory "
        "dump and in an RDB/AOF snapshot for as long as it lives.",
    ),
)


def build_at_rest(settings: Settings | None = None) -> AtRestReport:
    """Return the at-rest posture for the running configuration.

    Args:
        settings: The settings to read. Defaults to the process's live settings; passed
            explicitly by tests that need the verdict to follow the wiring rather than
            the environment the suite happens to run in.

    Returns:
        An :class:`AtRestReport` with one row per store, each carrying its verdict and
        the basis for it. A store whose setting is unset reads ``not_configured`` — it
        holds nothing, so it is neither a gap nor a success.
    """
    live = settings if settings is not None else get_settings()
    declared = _declared_control(live)
    stores: list[AtRestStore] = []
    for sid, name, setting, holds, note in _DECLARED:
        location = _location(live, setting)
        if not location:
            state, basis = EncryptionState.NOT_CONFIGURED, Basis.MEASURED
        elif declared is StorageEncryption.NONE:
            state, basis = EncryptionState.NONE, Basis.MEASURED
        else:
            state, basis = EncryptionState.COVERED, Basis.DECLARED
        stores.append(
            AtRestStore(
                id=sid,
                name=name,
                holds=holds,
                location=location,
                setting=setting,
                encryption=state,
                basis=basis,
                note=_with_measured_mode(sid, note, live) if location else note,
            )
        )
    return AtRestReport(
        generated_at=datetime.now(UTC).isoformat(),
        declared_control=declared,
        stores=stores,
        unencrypted=sum(1 for s in stores if s.encryption is EncryptionState.NONE),
        covered=sum(1 for s in stores if s.encryption is EncryptionState.COVERED),
        column_encryption=False,
        note=NOTE,
    )


def _declared_control(settings: Settings) -> StorageEncryption:
    """Return the operator's declaration, defaulting to ``none`` on anything unreadable.

    An unrecognised value is ``none``, never a guess in the reassuring direction: the
    whole point of this surface is that it cannot be more optimistic than the
    configuration, and a typo in an environment variable is exactly the configuration
    that would otherwise buy a green cell.
    """
    raw = str(getattr(settings, "storage_encryption", "") or "").strip().lower()
    try:
        return StorageEncryption(raw)
    except ValueError:
        return StorageEncryption.NONE


def _location(settings: Settings, setting: str) -> str:
    """Return the store's configured location with any credential stripped.

    A DSN carries a password, and this report is rendered in a console and served over
    an API. ``postgresql://u:pw@host/db`` is reduced to ``host/db`` by the same rule
    :func:`app.platform.residency._split` uses — everything before the last ``@`` goes.
    """
    raw = str(getattr(settings, setting, "") or "").strip()
    if not raw:
        return ""
    if "@" in raw:
        scheme, _, rest = raw.partition("://")
        return f"{scheme}://{rest.rpartition('@')[2]}" if rest else raw.rpartition("@")[2]
    return raw


def _with_measured_mode(store_id: str, note: str, settings: Settings) -> str:
    """Append the document store's **measured** file mode to its note.

    The one fact in this module Aegis can actually check: the store is on this
    filesystem, so a ``stat`` settles it rather than a claim. Reported because the
    difference matters — if a deployment ever loosened those files to 0644 the note
    would say so, where a hand-written "written 0600" would keep reassuring.

    Silent when the store has not been written to yet, and never fatal: this is a
    reporting surface and an unreadable directory is a fact about the filesystem, not a
    reason to fail a request.
    """
    if store_id != "document-store":
        return note
    root = Path(str(getattr(settings, "document_store_path", "") or ""))
    try:
        modes = {
            stat.S_IMODE(entry.stat().st_mode)
            for entry in root.rglob("*")
            if entry.is_file()
        }
    except OSError:
        return note
    if not modes:
        return f"{note} (No stored documents yet, so no file mode was measured.)"
    listed = ", ".join(f"0{mode:o}" for mode in sorted(modes))
    verdict = "as intended" if modes <= {0o600} else "LOOSER THAN 0600 — investigate"
    return f"{note} Measured file modes on this host: {listed} ({verdict})."


def at_rest_summary(settings: Settings | None = None) -> str:
    """Return the one-line boot summary, so the posture is stated where an operator looks.

    Logged at startup rather than only served on a route, for the reason
    :func:`app.data.session.verify_rls_enforcement` logs its verdict: a control whose
    absence is only visible to someone who goes looking for it is one nobody discovers
    until an auditor does.

    Args:
        settings: The settings to read; defaults to the live ones.

    Returns:
        A sentence naming the declared control and how many stores are unprotected.
    """
    report = build_at_rest(settings)
    if report.declared_control is StorageEncryption.NONE:
        return (
            f"Encryption at rest: NONE declared, and {report.unencrypted} store(s) hold "
            "tenant content in the clear. Aegis does no column-level encryption by "
            "decision (see app.platform.at_rest); the control is volume encryption under "
            "the data directory and the document store, plus encrypted backups. Declare "
            "it with AEGIS_STORAGE_ENCRYPTION=volume|provider once it is actually in "
            "place — this line reports what it is told, and reports nothing as the "
            "default."
        )
    return (
        f"Encryption at rest: {report.declared_control.value} declared by the operator, "
        f"covering {report.covered} store(s). DECLARED, not verified — Aegis runs above "
        "the filesystem and cannot see whether the block device under it is encrypted. "
        "No column-level encryption is in force, by decision."
    )
