"""The re-index command — rebuild the dense index from the rows that survived.

    python -m app.ingestion --reindex                 # every tenant, every document
    python -m app.ingestion --reindex --tenant 1
    python -m app.ingestion --reindex --document 7
    python -m app.ingestion --verify                  # audit only, write nothing

Why this is a *separate* command from the re-index that already existed
-----------------------------------------------------------------------

:func:`app.ingestion.reindex.reindex_corpus` is the scheduled, tenant-folded, durable
re-index and it stays exactly as it is: it re-runs every stage but ``parse``, which is
the right answer when the *chunker*, the *prefix* or the *embedding model* changed,
because all of those change what the chunks and vectors should be.

None of those changed here. The vector **engine** changed. The chunks are correct, the
embeddings beside them are correct, and the only thing missing is the derived index. For
that, re-running ``chunk`` and ``embed`` would delete and recompute 37 correct rows and
pay the provider a second time for text that has not changed, to arrive back at the
values already sitting in the column. Worse, it would need the parse artifact to do it,
so a corpus whose bytes had been pruned could not be recovered at all — even though
everything needed to recover it was still in the database.

So this command does the narrow thing the wide one cannot: it replays
``chunks.embedding`` into the search engine. No parse, no chunk, no embed, no provider
call, no spend. Seconds rather than minutes, and safe to run when you merely suspect.

Safety
------

* **Idempotent.** Point ids are derived from each chunk's content-addressed key, so a
  re-run overwrites the same rows. Running it twice leaves exactly one of everything.
* **Non-destructive.** Nothing is deleted first, so there is no window in which a
  tenant's index is empty — and a run that fails halfway leaves strictly more of the
  corpus searchable than it found, never less.
* **Gated on a real flag.** Bare ``python -m app.ingestion`` does nothing but print
  usage. The verb is required so the command cannot be triggered by a stray invocation.
* **Audited from the store.** Every run finishes by reading the index back and comparing
  it against the durable rows, and the process exits non-zero if they disagree. A
  re-index that cannot prove it worked is the thing that got us here.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.data.session import get_admin_engine
from app.ingestion.vector_index import (
    open_qdrant_client,
    rebuild_dense_index,
)

logger = logging.getLogger("app.ingestion")


def _parser() -> argparse.ArgumentParser:
    """Return the argument parser for the command."""
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion",
        description=(
            "Rebuild the dense vector index from the embeddings already stored in "
            "chunks.embedding. Never calls the embedding provider."
        ),
    )
    verb = parser.add_mutually_exclusive_group(required=True)
    verb.add_argument(
        "--reindex",
        action="store_true",
        help="publish the stored embeddings into the dense index",
    )
    verb.add_argument(
        "--verify",
        action="store_true",
        help="audit the index against the durable rows and write nothing",
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--tenant", type=int, help="restrict to one tenant id")
    scope.add_argument("--document", type=int, help="restrict to one document id")
    parser.add_argument(
        "--collection",
        default=None,
        help="dense collection to rebuild (defaults to the platform's)",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    """Execute the requested verb and return the process exit code.

    Reads through the **admin** engine on purpose. This is an operator tool whose whole
    job may be to rebuild every tenant at once, and a serving-role session would be
    limited by the ``tenant_isolation`` policy to whichever tenant happened to be bound —
    which for an unbound CLI process is none of them, so the rebuild would silently find
    nothing to do and report success. ``--tenant`` remains an explicit ``WHERE``, so
    scoping is something the operator asks for rather than something the connection
    happens to impose.

    Args:
        args: Parsed arguments.

    Returns:
        ``0`` when the two stores agree afterwards, ``1`` when they do not.
    """
    kwargs = {}
    if args.collection:
        kwargs["collection"] = args.collection

    sessionmaker = async_sessionmaker(get_admin_engine(), expire_on_commit=False)
    client = open_qdrant_client()
    try:
        async with sessionmaker() as session:
            report = await rebuild_dense_index(
                session,
                tenant_id=args.tenant,
                document_id=args.document,
                client=client,
                dry_run=args.verify,
                **kwargs,
            )
    finally:
        client.close()

    drift = report.drift
    print(
        f"documents={report.documents} rows={report.rows} "
        f"published={report.published} unembedded={len(report.unembedded)}"
    )
    if drift is not None:
        print(drift.describe())
    if report.unembedded:
        print(
            f"chunk ids still carrying the 'not embedded yet' sentinel: "
            f"{report.unembedded[:20]}"
            f"{' …' if len(report.unembedded) > 20 else ''}\n"
            "  These need the embed stage, not this command."
        )

    if drift is None:
        print("index not audited; refusing to report success")
        return 1
    if drift.missing:
        print(
            f"FAIL: {len(drift.missing)} chunk vector(s) the durable rows claim are "
            "not in the index."
        )
        return 1
    if drift.orphaned:
        print(
            f"WARN: {len(drift.orphaned)} point(s) in the index belong to no current "
            "chunk row. Retrieval can ground an answer in text the corpus no longer "
            "has; re-run after the owning documents are re-chunked."
        )
        return 1
    print("OK: every stored chunk embedding is present in the dense index.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the command.

    Args:
        argv: Argument vector; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
