"""The memory-write rail, in one place, so both drain paths use the same one.

## Why this module exists at all

The screen was defined privately inside ``app.main`` and passed to the 60-second
backstop sweeper, under a comment reading *"a screen the production path does not pass
is not a guardrail, it is a guardrail-shaped hole."*

The production path did not pass it.

``AgentDeps._run_consolidation`` — the drain the live agent loop actually fires after
every turn — called ``sweep_pending`` with no ``screen=``, and ``consolidate`` skips the
rail entirely when the screen is ``None``. The hot path also **wins the race every
time**: measured across every consolidation job on this deployment, each drained in
20–160 **milliseconds** with ``attempts=1``, while the screened sweeper runs on a
60-second timer and can never claim a job that is already ``DONE``.

The proof it never fired is one query::

    select op, count(*) from memory_write_log group by 1
    -->  ADD | 28        (zero REFUSED, ever)

This is the fourth declared-but-unbound seam in this codebase — after ``read_back_for``,
the first memory ``screen``, and ``max_tool_result_tokens``. The pattern is always the
same: the capability is real, tested, and reachable from one caller, and the caller that
matters passes ``None``. Keeping the screen in a module that neither drain owns is the
structural answer: there is no longer a "the other one's" screen to forget.

## What it screens, and why that is a different question from the input rail

``check_input`` runs on the user's raw turn and does hold — a live guardrail block was
observed on the question itself. This rail runs one layer in: over the **extracted
candidate fact**, after the model has decided what is worth remembering.

They are not redundant. An extractor can synthesise a directive that appears nowhere in
the turn it read, and a fact is written once and then recalled into every later prompt —
so an unscreened write is a prompt-injection payload with a much longer half-life than
the message that produced it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from aegis.guardrails.memory_write import MemoryWriteCandidate, MemoryWriteVerdict

__all__ = ["memory_write_screen"]


async def memory_write_screen(
    candidate: MemoryWriteCandidate,
) -> MemoryWriteVerdict:
    """Screen one candidate fact with the platform's own guardrail pipeline.

    A fresh :class:`~aegis.guardrails.Guardrails` per call rather than a shared one,
    matching how the other module-level rail helpers are built: the pipeline is cheap to
    construct and holds no per-call state worth reusing, and a long-lived instance in a
    background sweeper is a place for configuration to go stale.

    Args:
        candidate: The extracted fact proposed for the long-term store.

    Returns:
        A verdict that may rewrite the candidate's fields rather than merely allowing or
        refusing it — see :class:`~aegis.guardrails.memory_write.MemoryWriteVerdict`.
    """
    from aegis.guardrails import Guardrails

    return await Guardrails().check_memory_write(candidate)
