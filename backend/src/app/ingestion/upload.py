"""The upload path: bytes in, a durable document row and a running ingest out.

``POST /documents`` is the only way a tenant's document enters this platform, and the
order of operations here is the whole design. Each step exists because the step after it
is expensive, irreversible, or both:

1. **Sniff and cap.** A declared ``Content-Type`` is a claim by whoever sent the bytes;
   the magic number is the fact. A ``.docx`` renamed to ``.pdf`` accepted here becomes a
   parse failure minutes later on the single-slot CPU queue, having already occupied the
   worker every other tenant's document is waiting for.
2. **Deduplicate on ``(tenant_id, content_sha256)``.** The constraint already exists on
   the table; this is the check that makes a re-upload cheap rather than merely refused.
   The same bytes must not start a second ingest — parsing is CPU-bound at roughly a
   second a page and embedding is billed, so a duplicate that slips through costs real
   money and real minutes. **With one exception, and it is the way out of a trap:** a
   document that is ``FAILED`` with **no** ``job_runs`` row never had an ingest at all
   (step 5 below failed), and re-sending its bytes starts one. Without that, a single
   flaky moment at upload time left the file permanently unreachable — refused by the
   dedup, invisible to ``GET /jobs``, and so un-requeueable. See :func:`_restart_ingest`.
3. **Admit.** :func:`aegis.jobs.admit` runs *before* any execution exists, and it raises.
   A 200-page document with 80 tables is real money against a $100 budget, and the
   refusal a tenant can act on is the one that arrives at upload — not one that arrives
   half way through the embed stage with the parse already paid for.
4. **Store the bytes**, content-addressed, atomically (:mod:`app.ingestion.store`).
5. **Insert the row and commit**, then **start the workflow**. The row is the system of
   record: it exists, and is answerable to the tenant, whether or not the orchestrator can
   be reached. If the start fails the row is closed as ``FAILED`` with the reason on it,
   because a document sitting in ``PENDING`` behind no execution at all is precisely the
   silently-stranded state the durable substrate was built to end.

**Nothing here reimplements job machinery.** The claim, the lease, the retry, the
cancellation flag, the reconciler and the concurrency cap are Phase 3's; this module
calls :func:`aegis.jobs.admit` and ``start_workflow`` and does no queueing of its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from aegis.jobs import Document, JobRun, JobStatus, admit
from aegis.jobs.stages import DEFAULT_QUEUE
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.data import get_sessionmaker, set_tenant_scope
from app.ingestion.store import DocumentStore, sha256_of
from app.jobs.control import estimate_ingest_usd

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_DOCUMENT_BYTES",
    "PDF_MAGIC",
    "DocumentTooLarge",
    "UnsupportedDocumentType",
    "UploadOutcome",
    "UploadUnscopedError",
    "WorkflowStartFailed",
    "upload_document",
]

#: The largest upload accepted, in bytes. Sized from the phase's own fixtures — the
#: 126-page IRS instructions are the biggest real document the pipeline was measured on —
#: with generous headroom. It is a cap on *memory*, not on ambition: the bytes are held
#: whole to hash them, and an unbounded upload is an unbounded allocation in the API
#: process. A tenant with a larger corpus uploads it as several documents, which the
#: queue was built to absorb.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024

#: The first bytes of every PDF, by specification. Sniffed rather than trusted; see the
#: module docstring.
PDF_MAGIC = b"%PDF-"

#: The job type this upload creates, and therefore the key admission resolves its
#: concurrency cap from (``jobs.max_inflight.ingest``).
_JOB_TYPE = "ingest"


class UnsupportedDocumentType(ValueError):
    """The uploaded bytes are not a PDF, whatever the request declared (a **415**).

    Refused at the door rather than at the parse. The ingestion pipeline reads PDFs; a
    file it cannot read is not a document that fails slowly, it is a document that never
    should have occupied the CPU queue.
    """


class DocumentTooLarge(ValueError):
    """The upload exceeded :data:`MAX_DOCUMENT_BYTES` (a **413**)."""

    def __init__(self, cap: int) -> None:
        """Record the cap that was passed, so the caller can name it."""
        super().__init__(
            f"the upload exceeds the {cap} byte cap; split it into several documents"
        )
        self.cap = cap


class UploadUnscopedError(ValueError):
    """The uploader has no owning tenant, so the document would own no chunks (a **400**).

    ``chunks.tenant_id`` is ``NOT NULL`` by design: a null-tenant chunk is invisible to
    every tenant under the ``tenant_isolation`` predicate while still being indexed and
    paid for. So a platform principal with no tenant pin cannot upload a document *to
    nobody* — it has to say which tenant's corpus this belongs to.
    """


class WorkflowStartFailed(RuntimeError):
    """The document was stored but the orchestrator would not start its ingest.

    Carries the ``document_id`` so the caller can say which upload is affected. The row
    has already been closed as ``FAILED`` with this reason on it by the time this is
    raised — a stored document behind no execution and no explanation is the state this
    error exists to prevent.
    """

    def __init__(self, document_id: int, reason: str) -> None:
        """Record which document could not be started, and why."""
        super().__init__(reason)
        self.document_id = document_id
        self.reason = reason


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    """What one upload produced, as the route renders it.

    Attributes:
        document_id: The ``documents`` row.
        filename: The name the tenant uploaded it under.
        content_sha256: The digest that deduplicates it and addresses its bytes.
        size_bytes: How large it is.
        status: The row's :class:`aegis.jobs.JobStatus`, as its string.
        workflow_id: The execution ingesting it, or ``None`` when a duplicate upload
            found a row that had never been started.
        created: ``True`` when these bytes were new for this tenant and an ingest was
            started; ``False`` when an identical document already existed — in which case
            **no** second workflow was started, which is the guarantee the caller is being
            told about rather than a detail.
        restarted: ``True`` when these bytes matched a document that had been stored but
            whose ingest was never started at all, and this call started it. No row is
            created (``created`` stays ``False``) and no second execution is forked; see
            :func:`upload_document` for why the *dedup* path is where this belongs.
        title: The document's derived title, once the parse has run. ``None`` before it.
        doc_type: The tenant's own classification, or ``None``.
        doc_date: The date the document is about, or ``None``.
    """

    document_id: int
    filename: str
    content_sha256: str
    size_bytes: int
    status: str
    workflow_id: str | None
    created: bool
    restarted: bool = False
    title: str | None = None
    doc_type: str | None = None
    doc_date: date | None = None


def _outcome(
    document: Document, *, created: bool, restarted: bool = False
) -> UploadOutcome:
    """Project a document row onto the upload contract.

    Built while the row is still loaded, so the response does not depend on a mapped
    object surviving its session.

    Args:
        document: The row.
        created: Whether this call created it.
        restarted: Whether this call started an ingest for a stored document that had
            never had one.

    Returns:
        The outcome to return.
    """
    return UploadOutcome(
        document_id=document.id,
        filename=document.filename,
        content_sha256=document.content_sha256,
        size_bytes=document.size_bytes,
        status=document.status.value,
        workflow_id=document.workflow_id,
        created=created,
        restarted=restarted,
        title=document.title,
        doc_type=document.doc_type,
        doc_date=document.doc_date,
    )


async def _never_started(
    session: AsyncSession, *, document: Document, tenant_id: int | None
) -> bool:
    """Whether this document is stuck: closed ``FAILED`` with no execution behind it.

    The **durable** test for "the ingest never began", and it is durable on purpose. A
    ``job_runs`` row is written by :func:`app.jobs.activities.start_ingest`, the
    workflow's first activity, so its absence means no execution ever claimed this
    document — which is exactly the state ``POST /documents`` leaves when the bytes are
    stored and ``start_workflow`` then fails. Every other kind of failure (a stage that
    died, a cancelled run) *has* a ``job_runs`` row, is visible in ``GET /jobs``, and is
    re-queued through ``POST /jobs/{id}/requeue``; those must not be touched here.

    Args:
        session: The scoped session.
        document: The existing row.
        tenant_id: The owning tenant, applied as the app-level filter over RLS.

    Returns:
        ``True`` when the document is ``FAILED`` and owns no job run.
    """
    if document.status is not JobStatus.FAILED:
        return False
    # Two ways a job run names this document, and either one disqualifies it: the
    # ``document_id`` its payload carries, and the workflow id the row itself records. The
    # second is belt and braces — a run written by an older build, or by the re-queue path
    # with its nonce, still belongs to this document — and a false "never started" would
    # fork a second execution over a document that already has one.
    owned = JobRun.payload["document_id"].as_string() == str(document.id)
    if document.workflow_id:
        owned = owned | (JobRun.workflow_id == document.workflow_id)
    statement = select(JobRun.id).where(owned)
    if tenant_id is not None:
        statement = statement.where(JobRun.tenant_id == tenant_id)
    return (await session.execute(statement.limit(1))).scalar_one_or_none() is None


def _check_bytes(data: bytes) -> None:
    """Refuse an upload that is too large or is not a PDF.

    Args:
        data: The uploaded bytes.

    Raises:
        DocumentTooLarge: Over :data:`MAX_DOCUMENT_BYTES`.
        UnsupportedDocumentType: Not a PDF by its magic number.
    """
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentTooLarge(MAX_DOCUMENT_BYTES)
    if not data.startswith(PDF_MAGIC):
        raise UnsupportedDocumentType(
            "the uploaded bytes do not begin with %PDF-, so this is not a PDF whatever "
            "the request declared; the ingestion pipeline reads PDFs"
        )


async def _existing(
    session: AsyncSession, *, tenant_id: int | None, sha256: str
) -> Document | None:
    """Return this tenant's document with these bytes, if it already has one.

    The read carries an explicit tenant predicate *and* runs on a scoped session. Both
    are kept deliberately: the predicate is what an ungoverned deployment relies on, the
    policy is what a mistake in the predicate runs into.

    Args:
        session: The scoped session.
        tenant_id: The uploading tenant.
        sha256: The content digest.

    Returns:
        The existing row, or ``None``.
    """
    clause = (
        Document.tenant_id.is_(None)
        if tenant_id is None
        else Document.tenant_id == tenant_id
    )
    return (
        await session.execute(
            select(Document).where(clause, Document.content_sha256 == sha256)
        )
    ).scalar_one_or_none()


async def upload_document(
    *,
    tenant_id: int | None,
    user_id: int | None,
    filename: str,
    data: bytes,
    doc_type: str | None = None,
    doc_date: date | None = None,
    store: DocumentStore | None = None,
    start_workflow: object | None = None,
) -> UploadOutcome:
    """Store a document, admit its ingest, and start it.

    Args:
        tenant_id: The tenant that will own the document and its chunks. ``None`` is
            refused — see :class:`UploadUnscopedError`.
        user_id: The uploading principal, carried onto the workflow so the run's cost is
            attributable to a person and not only to a tenant.
        filename: The name the tenant uploaded it under. Recorded on the row; it never
            reaches the filesystem (see :mod:`app.ingestion.store`).
        data: The document's bytes.
        doc_type: The tenant's own classification of the document, or ``None``. One of
            the two D7 fields nothing but the uploader can honestly know.
        doc_date: The date the document is *about*, or ``None``. Never inferred from the
            upload time, which would stamp a 2019 contract as this year.
        store: The document store; the configured one when omitted.
        start_workflow: An awaitable ``(workflow_id, tenant_id, document_id, user_id)``
            starter. Injected only by tests that need to assert on what the orchestrator
            was — or was not — asked to do; production resolves the Temporal client.

    Returns:
        The outcome, whose ``created`` flag says whether this call started an ingest or
        found the identical document already present.

    Raises:
        UploadUnscopedError: The uploader has no tenant.
        UnsupportedDocumentType: The bytes are not a PDF.
        DocumentTooLarge: The upload is over the cap.
        AdmissionDeniedError: The tenant is at its in-flight ingest cap.
        BudgetExceededError: The tenant cannot afford the estimated ingest.
        WorkflowStartFailed: The orchestrator refused or could not be reached.
    """
    if tenant_id is None:
        raise UploadUnscopedError(
            "an upload needs an owning tenant: chunks.tenant_id is NOT NULL, so a "
            "document uploaded to no tenant would be indexed and paid for while being "
            "invisible to everyone"
        )
    _check_bytes(data)
    store = store or DocumentStore.from_settings()
    sha256 = sha256_of(data)

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        existing = await _existing(session, tenant_id=tenant_id, sha256=sha256)
        if existing is not None:
            # The idempotent path: the same bytes are already this tenant's document.
            # Nothing is admitted, nothing is started, and the bytes are re-stored only
            # if they went missing — which repairs an interrupted earlier upload without
            # ever creating a second row or a second execution.
            if not store.has(tenant_id=tenant_id, sha256=sha256):
                store.put(tenant_id=tenant_id, sha256=sha256, data=data)
            if await _never_started(
                session, document=existing, tenant_id=tenant_id
            ):
                return await _restart_ingest(
                    session,
                    document=existing,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    size_bytes=len(data),
                    start_workflow=start_workflow,
                )
            logger.info(
                "upload of %s by tenant %s is a duplicate of document %s; no second "
                "ingest started",
                filename,
                tenant_id,
                existing.id,
            )
            return _outcome(existing, created=False)

        estimate = await estimate_ingest_usd(
            session, size_bytes=len(data), tenant_id=tenant_id
        )
        # The gate. It raises, and nothing below it runs — so a refused upload leaves no
        # execution in the orchestrator and no bytes in the store to reconcile later.
        await admit(
            session,
            tenant_id=tenant_id,
            job_type=_JOB_TYPE,
            estimated_cost_usd=estimate,
        )

        store.put(tenant_id=tenant_id, sha256=sha256, data=data)
        document = Document(
            tenant_id=tenant_id,
            filename=filename,
            content_sha256=sha256,
            mime_type="application/pdf",
            size_bytes=len(data),
            doc_type=doc_type or None,
            doc_date=doc_date,
            status=JobStatus.PENDING,
        )
        session.add(document)
        try:
            await session.flush()
        except IntegrityError:
            # Two uploads of the same bytes raced past the check above. The database
            # settles it — that is what the unique constraint is for — and the loser
            # reports the winner's row rather than a 500 the tenant cannot act on.
            await session.rollback()
            await set_tenant_scope(session, tenant_id)
            duplicate = await _existing(session, tenant_id=tenant_id, sha256=sha256)
            if duplicate is None:
                raise
            return _outcome(duplicate, created=False)
        document_id = document.id
        # ``{job_type}:{tenant}:{document}`` — the shape an operator greps for in the
        # orchestrator's UI. No nonce: this is a document's *first* ingest and the
        # document id is unique, so the id is unique without one. A re-queue adds one,
        # because that genuinely is a second execution (see app.jobs.control).
        workflow_id = f"{_JOB_TYPE}:{tenant_id}:{document_id}"
        document.workflow_id = workflow_id
        await session.commit()
        outcome = _outcome(document, created=True)

    starter = start_workflow or _start_ingest_workflow
    try:
        await starter(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            document_id=document_id,
            user_id=user_id,
        )
    except Exception as exc:
        await _fail_document(
            document_id,
            tenant_id=tenant_id,
            reason=f"the ingest workflow could not be started: {exc}",
        )
        logger.exception("failed to start ingest workflow %s", workflow_id)
        raise WorkflowStartFailed(document_id, str(exc)) from exc

    logger.info(
        "document %s (%s, %d bytes) uploaded by tenant %s; ingest %s started "
        "(estimated $%.4f)",
        document_id,
        filename,
        len(data),
        tenant_id,
        workflow_id,
        estimate,
    )
    return outcome


async def _restart_ingest(
    session: AsyncSession,
    *,
    document: Document,
    tenant_id: int | None,
    user_id: int | None,
    size_bytes: int,
    start_workflow: object | None,
) -> UploadOutcome:
    """Start the ingest of a stored document that never got one — the way back.

    **The trap this opens.** ``POST /documents`` stores the bytes, commits the row, then
    starts the workflow. With the orchestrator down the start fails, the row is closed
    ``FAILED``, and a 503 is returned. Afterwards the document is unreachable by every
    route the platform has: re-uploading the identical bytes is refused by the
    ``(tenant_id, content_sha256)`` dedup, ``GET /jobs`` shows nothing (no execution ever
    claimed it, so there is no ``job_runs`` row), and ``POST /jobs/{id}/requeue`` therefore
    has no row to act on. One flaky moment at upload time and the file was dead until
    somebody edited the database by hand.

    **Why the dedup path rather than a new re-ingest route.** Three reasons, in order of
    weight. The remedy has to be reachable by someone who has the *file*, and re-uploading
    it is what a person does without being told; a new endpoint would need a screen, a
    client function and a portal entry before it helped anybody. The dedup branch is
    already the code that runs when a tenant re-sends bytes the platform has, so this is
    a correction to an existing answer ("no second ingest was started" — true, and it
    should have been) rather than a second, parallel way in. And a re-ingest route would
    have to answer "which documents may be re-ingested?", which is a policy question with
    a wrong answer available (re-parsing a healthy 126-page document costs real minutes
    and real money); the guard here has no such freedom.

    **Idempotent, on durable state.** The gate is
    :func:`_never_started` — ``FAILED`` *and* no ``job_runs`` row — and starting an
    execution is precisely what makes that row appear, so a second identical upload a
    moment later no longer qualifies. The **same** workflow id is reused rather than a
    fresh nonce, and that is the second lock: if a phantom execution does exist (the start
    reached the orchestrator and only the reply was lost) Temporal refuses the duplicate
    id, and the refusal is reported rather than forked into a second workflow walking one
    document.

    Admission runs first, exactly as on the create path: this is real, billable work
    starting, and a tenant at its cap must meet the gate here too.

    Args:
        session: The scoped session, inside the upload's transaction.
        document: The stuck row.
        tenant_id: Its owning tenant.
        user_id: The principal asking, for cost attribution on the new run.
        size_bytes: The document's size, for the budget estimate.
        start_workflow: The injected starter, or ``None`` for the real one.

    Returns:
        The outcome, with ``restarted=True`` and ``created=False``.

    Raises:
        AdmissionDeniedError: The tenant is at its in-flight ingest cap.
        BudgetExceededError: The tenant cannot afford the estimated ingest.
        WorkflowStartFailed: The orchestrator is still unreachable — in which case the
            row is closed ``FAILED`` again with the new reason, and this path stays open.
    """
    document_id = document.id
    estimate = await estimate_ingest_usd(
        session, size_bytes=size_bytes, tenant_id=tenant_id
    )
    await admit(
        session, tenant_id=tenant_id, job_type=_JOB_TYPE, estimated_cost_usd=estimate
    )
    # Reused, never re-minted: see the docstring. A document's *first* ingest is unique by
    # its document id, and reusing the id is what makes a duplicate start collide instead
    # of fork.
    workflow_id = document.workflow_id or f"{_JOB_TYPE}:{tenant_id}:{document_id}"
    document.workflow_id = workflow_id
    document.status = JobStatus.PENDING
    document.error = None
    # Read off the mapped row *before* the commit expires it. Touching an expired
    # attribute afterwards would emit a lazy refresh on an async session, which is the
    # ``MissingGreenlet`` this ordering exists to avoid.
    snapshot = (
        document.filename,
        document.content_sha256,
        document.size_bytes,
        document.title,
        document.doc_type,
        document.doc_date,
    )
    await session.commit()

    starter = start_workflow or _start_ingest_workflow
    try:
        await starter(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            document_id=document_id,
            user_id=user_id,
        )
    except Exception as exc:
        await _fail_document(
            document_id,
            tenant_id=tenant_id,
            reason=f"the ingest workflow could not be started: {exc}",
        )
        logger.exception("failed to restart ingest workflow %s", workflow_id)
        raise WorkflowStartFailed(document_id, str(exc)) from exc

    logger.info(
        "document %s had been stored with no execution behind it; ingest %s started for "
        "tenant %s (estimated $%.4f)",
        document_id,
        workflow_id,
        tenant_id,
        estimate,
    )
    name, sha, size, title, doc_type, doc_date = snapshot
    return UploadOutcome(
        document_id=document_id,
        filename=name,
        content_sha256=sha,
        size_bytes=size,
        status=JobStatus.PENDING.value,
        workflow_id=workflow_id,
        created=False,
        restarted=True,
        title=title,
        doc_type=doc_type,
        doc_date=doc_date,
    )


async def _start_ingest_workflow(
    *, workflow_id: str, tenant_id: int | None, document_id: int, user_id: int | None
) -> None:
    """Start the ingest workflow for one document.

    Args:
        workflow_id: The execution id, which is also the ``job_runs`` idempotency key.
        tenant_id: The owning tenant, threaded onto every activity argument the workflow
            builds — which is how the scope survives into a worker process that knows
            nothing about this request.
        document_id: The document to ingest.
        user_id: The uploading principal, for cost attribution.
    """
    from app.jobs.client import get_temporal_client  # noqa: PLC0415 - lazy
    from app.jobs.flows import INGEST_WORKFLOW  # noqa: PLC0415 - lazy
    from app.jobs.flows.contracts import IngestParams  # noqa: PLC0415 - lazy

    client = await get_temporal_client()
    await client.start_workflow(
        INGEST_WORKFLOW,
        IngestParams(tenant_id=tenant_id, document_id=document_id, user_id=user_id),
        id=workflow_id,
        task_queue=DEFAULT_QUEUE,
    )


async def _fail_document(document_id: int, *, tenant_id: int | None, reason: str) -> None:
    """Close a document as ``FAILED`` with the reason on the row.

    A best-effort write on its own connection: it runs after the upload transaction has
    committed, and the alternative to attempting it is a row left ``PENDING`` behind an
    execution that does not exist — which no reconciler would find, because the
    reconciler sweeps *job runs* and this document never produced one.

    Args:
        document_id: The document to close.
        tenant_id: Its owning tenant, for the scope.
        reason: What to record in ``documents.error``.
    """
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            await session.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(status=JobStatus.FAILED, error=reason)
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - the original failure is what the caller reports
        logger.exception(
            "could not record the start failure on document %s; it will read PENDING "
            "with no execution behind it",
            document_id,
        )
