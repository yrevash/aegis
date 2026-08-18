"""Where an uploaded document's bytes actually live, and how a stage finds them again.

``POST /documents`` accepts bytes and returns immediately; the parse happens minutes
later, in another process, on a queue with one slot. So the bytes have to be **durable
and addressable between the two**, and the ``documents`` row deliberately does not hold
them: a 126-page PDF is megabytes of ``bytea`` on the hottest tenant-scoped table in the
system, replicated into every backup and read past by every query that never wants it.

The store is therefore a content-addressed directory, and the address is the same
``content_sha256`` the ``documents`` row carries and the ``uq_documents_tenant_sha``
constraint deduplicates on. Three properties follow from that and each one is load-bearing:

* **Re-uploading identical bytes writes the same path.** The idempotency the constraint
  gives the *row* is the idempotency the filesystem gives the *bytes*, rather than a
  second scheme that can disagree with it.
* **A path cannot be forged from a filename.** The tenant's own file name never reaches
  the filesystem — a name is data on the row, not a directory entry — so no upload called
  ``../../etc/passwd`` can escape the root, and no tenant can address another tenant's
  document by guessing its name.
* **Tenants are separate subtrees.** ``documents.content_sha256`` is unique *per tenant*
  on purpose (two tenants uploading the same public filing are two independent documents
  with independent deletions), so the store partitions the same way. Sharing one blob
  between two tenants would make one tenant's delete the other tenant's data loss.

Writes are atomic: a temporary file in the same directory, then :func:`os.replace`, which
is atomic on POSIX and on Windows for a same-volume rename. A reader that arrives during
a write therefore sees the whole file or no file — never the half of it that had been
flushed, which parses as a corrupt PDF and looks exactly like a bad upload.

**The parse artifact lives here too**, beside the bytes. ``parse`` runs on the CPU queue
and ``chunk`` runs on the default queue, so they are different activities in different
transactions and possibly different processes; the structured blocks the parse recovered
have to survive that gap or the chunk stage would have to re-parse two hundred pages to
learn what the parse already knew. This is a **shared-filesystem** assumption and it is
stated rather than hidden: on the single-box posture the phase targets it holds trivially,
and a scaled deployment that splits the queues across machines must point
``DOCUMENT_STORE_PATH`` at shared storage (or run one process per document's whole
pipeline). The alternative — a second copy of the artifact in Postgres — trades a
megabytes-per-document write on a governed table for a constraint the demo box does not
need.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

__all__ = ["DocumentStore", "sha256_of"]

#: The suffix the uploaded bytes are stored under. Deliberately not ``.pdf``: the store
#: holds whatever was uploaded, and naming the file for a format nobody verified would be
#: the first lie in the chain.
_BYTES_SUFFIX = ".bin"

#: The suffix of the parse artifact written beside the bytes (see the module docstring).
_PARSE_SUFFIX = ".parse.json"

#: The subdirectory holding documents with no owning tenant. Spelled with a leading
#: underscore so it cannot collide with the ``t<id>`` form a real tenant produces.
_PLATFORM_DIR = "_platform"


def sha256_of(data: bytes) -> str:
    """Return the lower-case hex SHA-256 of ``data``.

    One function, used by the upload path and by the store, so the digest written to
    ``documents.content_sha256`` and the digest naming the file cannot be computed two
    different ways.

    Args:
        data: The bytes to digest.

    Returns:
        64 lower-case hex characters.
    """
    return hashlib.sha256(data).hexdigest()


class DocumentStore:
    """A content-addressed, tenant-partitioned store for uploaded bytes and their parse.

    Attributes:
        root: The directory everything is written under.
    """

    def __init__(self, root: Path | str) -> None:
        """Build a store rooted at ``root`` (created on first write, not here).

        Args:
            root: The directory to store documents under. Relative paths are resolved
                against the working directory, exactly like ``vector_store_path``.
        """
        self.root = Path(root)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> DocumentStore:
        """Build the store this deployment is configured to use.

        Args:
            settings: Application settings; defaults to the process singleton.

        Returns:
            The configured store.
        """
        settings = settings or get_settings()
        return cls(settings.document_store_path)

    def __repr__(self) -> str:  # pragma: no cover - developer affordance
        """Return the store and the directory it is rooted at."""
        return f"DocumentStore(root={str(self.root)!r})"

    # ------------------------------------------------------------------ addressing

    def _tenant_dir(self, tenant_id: int | None) -> Path:
        """Return the subtree owned by ``tenant_id``.

        Args:
            tenant_id: The owning tenant, or ``None`` for a platform-level document.

        Returns:
            The directory, which may not exist yet.
        """
        return self.root / (_PLATFORM_DIR if tenant_id is None else f"t{tenant_id}")

    @staticmethod
    def _checked_sha(sha256: str) -> str:
        """Return ``sha256`` if it is a real digest, else raise.

        The digest becomes a path segment, so this is the boundary that keeps a caller
        from writing one. It is not defence against the *tenant* — the tenant never
        supplies it — but against a future caller that passes a filename here by mistake,
        which would otherwise silently create a directory traversal.

        Args:
            sha256: The candidate digest.

        Returns:
            The digest, lower-cased.

        Raises:
            ValueError: If it is not 64 hexadecimal characters.
        """
        value = sha256.strip().lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError(
                f"{sha256!r} is not a SHA-256 digest; it names a path segment in the "
                "document store and must never be a caller-supplied string"
            )
        return value

    def path_for(self, *, tenant_id: int | None, sha256: str) -> Path:
        """Return the path the bytes of one document occupy.

        Fanned out one level by the digest's first two characters: a flat directory of
        tens of thousands of entries is slow to list on every filesystem and pathological
        on some.

        Args:
            tenant_id: The owning tenant, or ``None``.
            sha256: The content digest.

        Returns:
            The absolute-or-relative path, which may not exist.

        Raises:
            ValueError: If ``sha256`` is not a digest.
        """
        digest = self._checked_sha(sha256)
        return self._tenant_dir(tenant_id) / digest[:2] / f"{digest}{_BYTES_SUFFIX}"

    def artifact_path_for(self, *, tenant_id: int | None, sha256: str) -> Path:
        """Return the path of the parse artifact belonging to one document.

        Args:
            tenant_id: The owning tenant, or ``None``.
            sha256: The content digest.

        Returns:
            The artifact path, which may not exist.
        """
        return self.path_for(tenant_id=tenant_id, sha256=sha256).with_name(
            f"{self._checked_sha(sha256)}{_PARSE_SUFFIX}"
        )

    # ------------------------------------------------------------------ read / write

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        """Write ``data`` to ``path`` so no reader can observe a partial file.

        Args:
            path: The destination.
            data: The bytes to write.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".partial")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def put(self, *, tenant_id: int | None, sha256: str, data: bytes) -> Path:
        """Store one document's bytes and return where they landed.

        Idempotent by construction: the path is the content's own digest, so storing the
        same bytes twice writes the same file. It is re-written rather than skipped —
        the write is atomic and the content is identical, so this repairs a truncated
        file from an interrupted earlier run instead of trusting its existence.

        Args:
            tenant_id: The owning tenant, or ``None`` for a platform-level document.
            sha256: The content digest, which must be the digest **of these bytes**.
            data: The document's bytes.

        Returns:
            The path the bytes now occupy.

        Raises:
            ValueError: If ``sha256`` is not a digest, or is not the digest of ``data`` —
                a mismatch means the row and the file would disagree about what was
                stored, and every later read would be of something nobody checked.
        """
        digest = self._checked_sha(sha256)
        actual = sha256_of(data)
        if actual != digest:
            raise ValueError(
                f"refusing to store bytes under {digest}: they hash to {actual}. The "
                "documents row and the stored file would then describe different content."
            )
        path = self.path_for(tenant_id=tenant_id, sha256=digest)
        self._atomic_write(path, data)
        logger.debug("stored %d bytes for tenant %s at %s", len(data), tenant_id, path)
        return path

    def read(self, *, tenant_id: int | None, sha256: str) -> bytes:
        """Return one document's stored bytes.

        Args:
            tenant_id: The owning tenant, or ``None``.
            sha256: The content digest.

        Returns:
            The bytes.

        Raises:
            FileNotFoundError: If nothing is stored under that address.
        """
        return self.path_for(tenant_id=tenant_id, sha256=sha256).read_bytes()

    def has(self, *, tenant_id: int | None, sha256: str) -> bool:
        """Return whether bytes are stored for this document.

        Args:
            tenant_id: The owning tenant, or ``None``.
            sha256: The content digest.

        Returns:
            ``True`` when the file exists.
        """
        return self.path_for(tenant_id=tenant_id, sha256=sha256).is_file()

    def put_artifact(self, *, tenant_id: int | None, sha256: str, payload: str) -> Path:
        """Write the parse artifact for one document, atomically.

        Args:
            tenant_id: The owning tenant, or ``None``.
            sha256: The document's content digest.
            payload: The serialised artifact (see :mod:`app.ingestion.artifacts`).

        Returns:
            The path written.
        """
        path = self.artifact_path_for(tenant_id=tenant_id, sha256=sha256)
        self._atomic_write(path, payload.encode("utf-8"))
        return path

    def read_artifact(self, *, tenant_id: int | None, sha256: str) -> str:
        """Return the parse artifact for one document.

        Args:
            tenant_id: The owning tenant, or ``None``.
            sha256: The document's content digest.

        Returns:
            The serialised artifact.

        Raises:
            FileNotFoundError: If the document has not been parsed by a run whose output
                still exists. The caller turns that into a non-retryable stage failure
                naming the parse, rather than silently re-parsing — a chunk stage that
                quietly re-ran the parse would hide the fact that the artifact is gone.
        """
        return self.artifact_path_for(tenant_id=tenant_id, sha256=sha256).read_text(
            encoding="utf-8"
        )
