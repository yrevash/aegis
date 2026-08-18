"""What a stage *found*, as opposed to what it *set* — the second channel out of a handler.

A stage handler returns a mapping of :class:`aegis.jobs.Document` columns, and the
substrate applies it through an allow-list. That is exactly right for state — the page
count, the chunk count, the parse confidence — and it has no room at all for the other
half of what a stage learns: why the quality gate scored the parse the way it did, whether
OCR ran and on what evidence, how many tables were summarised and how many model calls
that cost. None of those is a column and none of them should become one; they are facts
about *one attempt*, and they belong in the append-only run record (task 4.12) that
:mod:`app.jobs.ingest_log` writes and :mod:`app.ingestion.progress` reads back.

So handlers report them here, and the substrate collects them::

    # in the substrate
    with collect_stage_facts() as facts:
        columns = await handler(session, ...)
    # `facts` now holds whatever the handler chose to report

    # in the handler
    report_stage_facts(parser=parsed.parser, ocr={"enabled": ..., "reason": ...})

**Why this is not in :mod:`aegis.jobs.stages`.** That module is read from *inside* the
orchestrator's workflow sandbox, which re-imports the defining module on every workflow
task, and its discipline is that it does no import-time work beyond building frozen
dataclasses. A :class:`~contextvars.ContextVar` is import-time mutable state: re-executing
that module in a sandbox would mint a *fresh* variable each time, so a handler and its
collector could end up looking at two different ones with nothing anywhere reporting a
problem. A workflow has no reason to read this module at all, so keeping it out of the one
the sandbox re-runs removes the question. ``aegis/tests/jobs/test_stages.py`` asserts the
stage contract's import list against its own AST, which is what makes that boundary hold
rather than merely be described.

**Why a context variable rather than a module global.** :data:`aegis.jobs.IO_QUEUE` runs
32 activities at once in one process. A global dict would interleave one document's
evidence into another's, and the resulting log would be plausible and wrong — the worst
combination a log can be.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

__all__ = ["collect_stage_facts", "report_stage_facts"]

#: The mapping the currently-running stage reports what it found into.
#:
#: Holds a **mutable dict**, and that is load-bearing rather than incidental: the runner
#: may await the handler in a child :class:`asyncio.Task`, and a task copies the context —
#: which copies the *reference*. A handler that rebound the variable would write somewhere
#: the substrate cannot see; a handler that mutates the dict writes where it can.
_STAGE_FACTS: ContextVar[dict[str, Any] | None] = ContextVar(
    "aegis_stage_facts", default=None
)


@contextmanager
def collect_stage_facts() -> Iterator[dict[str, Any]]:
    """Open a scope in which :func:`report_stage_facts` is recorded, and yield the record.

    The substrate wraps one stage attempt in this and writes what comes out to the durable
    run record. Outside it, reporting is a no-op — so a handler invoked directly by a test,
    or by the scheduled re-index, behaves identically and needs no double.

    Yields:
        The mapping the handler will fill in. It is the caller's to read once the handler
        has returned, and is empty when the handler reported nothing.
    """
    facts: dict[str, Any] = {}
    token = _STAGE_FACTS.set(facts)
    try:
        yield facts
    finally:
        _STAGE_FACTS.reset(token)


def report_stage_facts(**facts: Any) -> None:  # noqa: ANN401 - JSON-shaped evidence
    """Record what this stage found, for the durable log a tenant reads.

    **Not a second return channel for record-layer columns**, and the split is the point:
    what a handler *returns* is applied to the ``documents`` row and is therefore state;
    what it reports here is evidence about the attempt, which belongs in an append-only
    log and nowhere on a mutable row.

    Silently a no-op outside :func:`collect_stage_facts`. Deliberately: a handler must not
    have to know whether the substrate, a test or the re-index loop is calling it, and
    losing a log line is never a reason to fail work that succeeded.

    Args:
        **facts: JSON-serialisable values, merged into the current scope's record. A key
            reported twice keeps the later value.
    """
    current = _STAGE_FACTS.get()
    if current is None:
        return
    current.update(facts)
