"""Where D8's "one model call per table" becomes one model call *ever*.

:mod:`aegis.ingestion.tables` knows how to describe a table and when a table is worth
describing. This module is the half that needs a database: the ``table_summaries`` cache
that turns a per-document cost into a per-table-content cost.

The arithmetic is the reason it exists. The phase's own risk section names it — *"a
200-page document with 80 tables is real money against $100"* — and the naive shape of
this feature bills that document again on every re-upload and again on every 4.13
re-index, neither of which produced a single new table. Keyed on
:func:`aegis.ingestion.tables.table_digest`, the second pass over the same corpus costs
nothing at all, and the ``chunk`` stage's idempotency contract (run it twice, get one set
of chunks) extends to its spend: run it twice, pay once.

Three properties worth stating because each fails silently otherwise:

**A table below the threshold is never sent.** :meth:`TableSummaryPolicy.wants_summary`
is consulted before the cache is even read, so a small table costs neither a call nor a
round trip, and the chunk records *why* it has no summary rather than leaving "cheap" and
"broken" looking identical.

**The writes share the caller's transaction.** The stage handler rule is that a handler
writes on the session it was given; a cache is not an exception to it. A chunk stage that
rolls back therefore un-writes its cache rows too, which is the correct direction: the
alternative is a cache claiming to hold summaries for chunks that do not exist.

**A failed summary is not a failed ingest.** The grid is the answer to most questions a
table is asked; the summary is how the question finds it. Losing the second is a
retrieval-quality regression on one chunk, and failing the document over it would be a
worse trade — so the failure is logged, recorded on the chunk, and the ingest continues.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from aegis.ingestion.tables import TableRef, TableSummaryPolicy, summarise_table
from aegis.jobs.models import TableSummary
from aegis.retrieval.chunker import SectionChunk
from aegis.retrieval.protocols import CompleteFn
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

__all__ = ["TableSummaryReport", "summarise_document_tables"]


@dataclass(frozen=True)
class _PendingTable:
    """One distinct table in a document, and the context its summary is written from."""

    ref: TableRef
    grid: str
    section: str


@dataclass
class TableSummaryReport:
    """What the summarisation pass did, per distinct table and in total.

    Keyed by digest rather than by chunk, because the digest is what the work is done
    per: a table appearing twice in one document is one entry here and two chunks that
    read from it.

    Attributes:
        summaries: Digest to the sentence(s) that will be embedded in front of the grid.
        reasons: Digest to why it has no summary — below the threshold, disabled, or a
            failure. Recorded on the chunk so an empty summary is never ambiguous.
        model_calls: Model calls actually made. The number the cache exists to hold down,
            and the number the tests assert on.
        cache_hits: Distinct tables served from ``table_summaries`` instead.
        skipped: Distinct tables the policy declined to summarise.
    """

    summaries: dict[str, str] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    model_calls: int = 0
    cache_hits: int = 0
    skipped: int = 0

    def summary_for(self, table: TableRef | None) -> str:
        """Return the summary for ``table``, or ``""`` when it has none.

        Args:
            table: The chunk's table, or ``None`` when the chunk is prose.

        Returns:
            The summary text, or the empty string.
        """
        if table is None:
            return ""
        return self.summaries.get(table.digest, "")

    def reason_for(self, table: TableRef | None) -> str:
        """Return why ``table`` has no summary, or ``""`` when it has one.

        Args:
            table: The chunk's table, or ``None`` when the chunk is prose.

        Returns:
            The recorded reason, or the empty string.
        """
        if table is None:
            return ""
        return self.reasons.get(table.digest, "")


def _distinct_tables(pieces: Sequence[SectionChunk]) -> dict[str, _PendingTable]:
    """Collapse a document's table chunks to one entry per distinct table content.

    Args:
        pieces: The document's chunks, in reading order.

    Returns:
        Digest to the first chunk that carried it. First rather than last so the section
        recorded is the one the table is actually introduced under.
    """
    tables: dict[str, _PendingTable] = {}
    for piece in pieces:
        if piece.table is None or piece.table.digest in tables:
            continue
        tables[piece.table.digest] = _PendingTable(
            ref=piece.table, grid=piece.text, section=piece.section
        )
    return tables


async def _cached(
    session: AsyncSession, *, tenant_id: int, digests: Sequence[str]
) -> dict[str, str]:
    """Read this tenant's already-paid-for summaries for ``digests``.

    Args:
        session: The scoped session, inside the stage's transaction.
        tenant_id: The owning tenant — on the predicate as well as in the RLS scope,
            because the scope is a property of the connection and this is a property of
            the query, and a read that relies only on the former is one ``set_config``
            away from being corpus-wide.
        digests: The table digests wanted.

    Returns:
        Digest to summary, for the ones already held.
    """
    if not digests:
        return {}
    rows = (
        await session.execute(
            select(TableSummary.digest, TableSummary.summary).where(
                TableSummary.tenant_id == tenant_id,
                TableSummary.digest.in_(list(digests)),
            )
        )
    ).all()
    return {digest: summary for digest, summary in rows}


async def _remember(
    session: AsyncSession,
    *,
    tenant_id: int,
    table: TableRef,
    summary: str,
    policy: TableSummaryPolicy,
) -> None:
    """Write one summary into the cache, tolerating a concurrent writer.

    ``ON CONFLICT DO NOTHING`` rather than an upsert: two ingests of the same corpus can
    reach the same table at the same time, and the loser's sentence is not better than
    the winner's — it is a second model call's worth of the same thing. Overwriting would
    also rewrite a summary other chunks have already been embedded against.

    Args:
        session: The scoped session, inside the stage's transaction.
        tenant_id: The owning tenant.
        table: The table this describes.
        summary: The generated sentence(s).
        policy: The policy whose role paid for it.
    """
    await session.execute(
        pg_insert(TableSummary)
        .values(
            tenant_id=tenant_id,
            digest=table.digest,
            summary=summary,
            row_count=table.rows,
            col_count=table.cols,
            model_role=str(policy.role),
        )
        .on_conflict_do_nothing(constraint="uq_table_summaries_tenant_digest")
    )


async def summarise_document_tables(
    session: AsyncSession,
    pieces: Sequence[SectionChunk],
    *,
    tenant_id: int,
    complete: CompleteFn | None,
    policy: TableSummaryPolicy,
    title: str = "",
) -> TableSummaryReport:
    """Summarise this document's tables, paying for each distinct one at most once.

    The order is deliberate and is the whole cost control: **threshold, then cache, then
    model.** A table that fails the threshold never reaches the cache read; a table the
    cache holds never reaches the gateway.

    Calls are made one at a time rather than gathered. A document with eighty tables
    would otherwise open eighty concurrent gateway requests from inside a database
    transaction, which converts a bounded ingest into a rate-limit incident and a
    long-lived idle-in-transaction connection at the same time.

    Args:
        session: The scoped session, inside the ``chunk`` stage's transaction.
        pieces: The document's chunks, in reading order.
        tenant_id: The tenant that owns the document and pays for the calls.
        complete: The injected chat-completion callable. ``None`` is legal **only** when
            nothing clears the threshold — a document of prose, or a deployment with
            ``policy.enabled`` off — so that the ``chunk`` stage on a text-only corpus
            never resolves a model gateway it will not use.
        policy: When to summarise, and with which role.
        title: The document's title, for the prompt's context.

    Returns:
        A :class:`TableSummaryReport` — the summaries, the reasons for the absences, and
        the counts the ingest log reports.

    Raises:
        ValueError: If a table clears the threshold and no completer was supplied. That
            is a wiring bug, and the alternative — silently recording every table as
            unsummarised — is the version of it nobody finds.
    """
    report = TableSummaryReport()
    tables = _distinct_tables(pieces)
    if not tables:
        return report

    wanted: dict[str, _PendingTable] = {}
    for digest, pending in tables.items():
        if policy.wants_summary(pending.ref):
            wanted[digest] = pending
        else:
            report.reasons[digest] = policy.reason_to_skip(pending.ref)
            report.skipped += 1

    cached = await _cached(session, tenant_id=tenant_id, digests=list(wanted))
    report.summaries.update(cached)
    report.cache_hits = len(cached)

    missing = [
        (digest, pending) for digest, pending in wanted.items() if digest not in cached
    ]
    if missing and complete is None:
        raise ValueError(
            f"{len(missing)} table(s) clear the summary threshold and are not cached, "
            "but no completer was supplied; a caller passes None only when it has "
            "already established that nothing will need to be summarised"
        )
    # Narrowed by the guard above: ``missing`` is non-empty only when a completer exists.
    completer: CompleteFn = complete  # type: ignore[assignment]
    for digest, pending in missing:
        try:
            summary = await summarise_table(
                pending.grid,
                table=pending.ref,
                complete=completer,
                policy=policy,
                title=title,
                section=pending.section,
            )
        except Exception as exc:  # noqa: BLE001 - the grid survives; the ingest must too
            report.reasons[digest] = f"the summary call failed: {exc}"
            logger.warning(
                "table summary failed for a %dx%d table (digest %s): %s",
                pending.ref.rows,
                pending.ref.cols,
                digest[:12],
                exc,
            )
            continue
        report.model_calls += 1
        if not summary:
            report.reasons[digest] = "the model returned no usable summary"
            logger.warning(
                "table summary came back empty for a %dx%d table (digest %s)",
                pending.ref.rows,
                pending.ref.cols,
                digest[:12],
            )
            continue
        report.summaries[digest] = summary
        await _remember(
            session, tenant_id=tenant_id, table=pending.ref, summary=summary, policy=policy
        )

    logger.info(
        "table summaries: %d distinct table(s), %d model call(s), %d cache hit(s), "
        "%d below threshold",
        len(tables),
        report.model_calls,
        report.cache_hits,
        report.skipped,
    )
    return report
