"""The re-index handler — rebuild a tenant's index from the parse artifacts, never the bytes.

Phase 3 shipped everything *around* this file: the Temporal Schedule that fires on a
cadence, the per-tenant workflow id that folds a burst of requests into one run, the timer
reset and its ceiling, and the ``job_runs`` row that records what happened
(:mod:`app.jobs.flows.reindex`, :mod:`app.jobs.reindex`). It deliberately shipped with
**no** default handler, because a re-index that recorded ``succeeded`` for work nothing
performed would be the platform lying to a tenant about the freshness of their own index.
This module is the work.

What a re-index is, and what it is not
--------------------------------------

A document's *bytes* do not change. What changes underneath them does: the embedding model
and its dimensionality, the chunker's packing, the D7 prefix's fields and shape, the
extractor behind the graph arm, and the vector store the dense arm searches — which can be
rebuilt, migrated or lost entirely. Re-indexing is re-running the pipeline over everything
that can change without the document changing.

So it re-runs **every stage except ``parse``**. That single exclusion is the whole
performance argument: the parse artifact was written beside the bytes by the ``parse``
stage (:mod:`app.ingestion.store`), and re-deriving it costs 0.43–3.20 s a page — six
minutes for a 126-page document, to reproduce a tree already on disk. Skipping it is not a
shortcut; re-parsing would be the bug.

``graph`` is re-run and is not optional. The ``chunk`` stage is delete-then-insert, so the
entities and relations the ``graph`` stage wrote onto ``chunks.meta`` are destroyed the
moment a re-index re-chunks a document. Stopping after ``index`` would therefore leave the
graph arm silently empty for every re-indexed document — a re-index that quietly deletes
one of three retrieval arms, which is exactly the class of failure this codebase keeps
finding.

All of it in one transaction, and what that does and does not cover
-------------------------------------------------------------------

:func:`app.jobs.reindex.run_reindex` hands the handler the substrate's session, inside the
single transaction the ``job_runs`` row will be written in. So a document whose parse
artifact is missing fails the run **and rolls the whole thing back**: the tenant keeps the
corpus they had rather than a corpus where half the documents are indexed under a new
embedder and half under the old one. Partial success is the worst of the three outcomes
here and the transaction is what makes it unreachable.

One thing the transaction does not cover, stated rather than discovered: the ``index``
stage publishes to a vector store outside PostgreSQL, and a rollback does not un-publish.
Because chunks are published under content-addressed, tenant-prefixed ids, a re-publish is
an overwrite of the same keys — so the next successful run converges the store rather than
duplicating into it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from aegis.jobs.models import Document, JobStatus
from aegis.jobs.stages import INGEST_STAGES, stage_handler
from aegis.retrieval.corpus import bump_corpus_version
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.exceptions import ApplicationError

# The re-index applies a handler's ``documents`` column updates exactly as the ingest
# substrate does — same allow-list, same refusal. Imported rather than re-stated because
# two copies of an allow-list drift, and the one that drifts is the one nobody is looking
# at: a re-index whose chunker packs differently must move ``chunk_count`` or the tenant's
# document page reports a number no longer true of any row.
from app.jobs.activities import _validated_updates
from app.jobs.reindex import register_reindex_handler

logger = logging.getLogger(__name__)

__all__ = [
    "REINDEX_STAGES",
    "REPARSE_STAGE",
    "register_corpus_reindex_handler",
    "reindex_corpus",
]

#: The one stage a re-index does not re-run. Named as a constant rather than spelled at
#: the filter below so that the reason travels with it: this is the stage whose input is
#: the uploaded bytes, and its output — the parse artifact — is already durable beside
#: them. Every other stage's input is something that can change under a document that has
#: not.
REPARSE_STAGE = "parse"

#: The stages a re-index runs, in the pipeline's own declared order.
#:
#: Derived from :data:`aegis.jobs.INGEST_STAGES` rather than listed, so a stage added to
#: the pipeline is re-indexed by default. That default is the safe one: a new stage left
#: out of the re-index would be a stage whose output silently rots, and rot that nothing
#: reports is this codebase's standing defect class.
REINDEX_STAGES: tuple[str, ...] = tuple(
    spec.name for spec in INGEST_STAGES if spec.name != REPARSE_STAGE
)


def _handler(stage: str):  # noqa: ANN202 - StageHandler, declared in aegis.jobs.stages
    """Return the registered handler for ``stage``, or fail the run non-retryably.

    A re-index reaching an unregistered stage means the process was never composed —
    ``register_ingest_handlers`` did not run — and no number of retries composes it. The
    substrate makes the same call for the same reason when a stage handler is missing
    mid-ingest; matching it here keeps one failure shape for one cause.

    Args:
        stage: The stage name.

    Returns:
        The registered handler.

    Raises:
        ApplicationError: Non-retryable, when nothing is registered for ``stage``.
    """
    try:
        return stage_handler(stage)
    except LookupError as exc:
        raise ApplicationError(
            str(exc), type="UnregisteredStage", non_retryable=True
        ) from exc


async def _reindexable_documents(session: AsyncSession) -> list[int]:
    """Return the ids of the tenant's fully-ingested documents, in ingestion order.

    Read through the bound scope with no ``WHERE tenant_id``: the ``tenant_isolation``
    policy on the connection is what limits this to one tenant's documents, so the read is
    part of the proof that the policy works rather than a Python filter that would pass
    either way.

    ``SUCCEEDED`` is the same visibility test
    :func:`app.jobs.reindex.request_tenant_reindex` counts on before it asks for a run at
    all — deliberately, so the cadence never wakes a worker for documents this function
    would then decline to touch.

    Args:
        session: The scoped session, inside the run's transaction.

    Returns:
        The document ids to rebuild.
    """
    return list(
        (
            await session.execute(
                select(Document.id)
                .where(Document.status == JobStatus.SUCCEEDED)
                .order_by(Document.id)
            )
        )
        .scalars()
        .all()
    )


async def reindex_corpus(
    session: AsyncSession,
    *,
    tenant_id: int | None,
    folded: int,
    reasons: tuple[str, ...],
) -> Mapping[str, Any]:
    """Rebuild every ingested document's chunks, embeddings and index for one tenant.

    Runs :data:`REINDEX_STAGES` — the pipeline without ``parse`` — over each document
    through the **registered** stage handlers, which is what makes this a re-run of the
    real ingest rather than a second implementation of it that can drift from it. Each of
    those handlers is idempotent by construction (``chunk`` is delete-then-insert,
    ``enrich`` is guarded by its own flag, ``embed``/``graph`` are keyed updates, ``index``
    republishes content-addressed ids), so running this twice over an unchanged corpus
    leaves exactly one of everything.

    The corpus version is bumped once at the end, for the same reason an ingest bumps it:
    a corpus rebuilt under a different embedder, chunker or prefix answers questions
    differently, and an answer cached before the rebuild is exactly as stale as one cached
    before an upload. One bump rather than one per document — the tenant's corpus changed
    once, and the caches are keyed per tenant, so a per-document bump would be the same
    invalidation performed N times.

    Args:
        session: The scoped session the substrate opened, inside the run's single
            transaction.
        tenant_id: The tenant whose corpus to rebuild.
        folded: How many requests this run stands for.
        reasons: Those requests' reasons, in arrival order.

    Returns:
        What it did — documents rebuilt, stages run, the chunk total afterwards and the
        corpus version the tenant is now on — recorded on ``job_runs.result``.

    Raises:
        ApplicationError: Whatever a stage handler raises, unchanged. A missing parse
            artifact or a document that has lost its owning tenant fails the run and rolls
            it back, rather than leaving the corpus half rebuilt under two embedders.
    """
    document_ids = await _reindexable_documents(session)
    if not document_ids:
        logger.info(
            "re-index for tenant %s: no ingested document is visible, so nothing was "
            "rebuilt and the corpus version is left where it is",
            tenant_id,
        )
        return {
            "documents": 0,
            "chunks": 0,
            "stages": list(REINDEX_STAGES),
            "folded": folded,
            "corpus_version": None,
        }

    chunks = 0
    for document_id in document_ids:
        for stage in REINDEX_STAGES:
            updates = await _handler(stage)(
                session, tenant_id=tenant_id, document_id=document_id, stage=stage
            )
            applied = _validated_updates(stage, updates)
            if applied:
                await session.execute(
                    update(Document).where(Document.id == document_id).values(**applied)
                )
            chunks += int(applied.get("chunk_count", 0) or 0)

    version = bump_corpus_version(tenant_id)
    logger.info(
        "re-indexed %d document(s) (%d chunk(s)) for tenant %s over stages %s, folding "
        "%d request(s) %s; corpus version is now %d",
        len(document_ids),
        chunks,
        tenant_id,
        ", ".join(REINDEX_STAGES),
        folded,
        list(reasons),
        version,
    )
    return {
        "documents": len(document_ids),
        "chunks": chunks,
        "stages": list(REINDEX_STAGES),
        "folded": folded,
        "corpus_version": version,
    }


def register_corpus_reindex_handler() -> None:
    """Install :func:`reindex_corpus` as the platform's re-index handler.

    Called from the same two composition roots that call
    :func:`app.ingestion.stages.register_ingest_handlers` — ``app.main``'s lifespan for
    the in-process worker and the ``__main__`` guard in :mod:`app.jobs.worker` for the
    standalone one — and for the same reason it is called there rather than inside
    ``run_workers``: a host or a test that has registered its own handler must not have it
    silently replaced by starting a worker.

    Safe to call twice; registration is an assignment.
    """
    register_reindex_handler(reindex_corpus)
