"""Which prompt version a run actually used — a bounded, in-process attribution log.

§7.7 owes an operator four answers about their prompt: what is live, what came before,
how to make a different version live, and **which version a given run was served**. The
first three are rows in ``prompt_versions``. The fourth is not: nothing in this platform
records, per run, the prompt the model was handed. It could be *inferred* from
``activated_at`` windows, but that inference is a lie the moment a rollback clears an
``activated_at`` — and an inferred audit trail is worse than none, because it reads as
evidence.

So the run records it, at the moment it is decided, and this module is where it lands.

**What this is, stated so nobody oversells it.** A ring of the most recent
:data:`_CAPACITY` runs *in this process, since it started*. It is the same honesty the
latency window already ships with, and the same shape: no runs yet reads as an empty
state, never as zeros. It is deliberately not a table — a per-run INSERT on the hot path
buys durability with latency and a new failure mode, and the durable home for this
already exists in ``run_events`` (:mod:`aegis.runs.models`), which agent runs are not yet
written to. When they are, this becomes a projection of the log rather than a second
source, and the endpoint's answer does not change.

The tenant is part of every entry and every read: :func:`recent` filters to one scope, so
an operator reading the attribution of "recent runs" reads their own tenant's runs, and a
run id belonging to another tenant reads as *unknown* rather than as somebody else's row.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = ["PromptRunRecord", "clear", "recent", "record", "resolve"]

#: How many runs the ring holds. Sized for a demo/console session rather than for
#: analytics — the durable answer is ``run_events``, not this.
_CAPACITY = 500

_LOCK = threading.Lock()
_RUNS: deque[PromptRunRecord] = deque(maxlen=_CAPACITY)


@dataclass(frozen=True)
class PromptRunRecord:
    """One run, and the prompt version it was served.

    Attributes:
        run_id: The run's id — the same one the stream, the trace and the console use.
        tenant_id: The run's sealed tenant scope (``None`` = a platform run).
        prompt_key: The registry key resolved (the persona).
        version: The active version served, or ``None`` when the run fell back to the
            adapter's shipped prompt. ``None`` is a real, meaningful answer here and is
            rendered as such — "the shipped prompt", not "unknown".
        source: ``"registry"`` when a promoted version was served, ``"floor"`` when the
            adapter's shipped prompt was.
        ts: When the resolution happened (run start).
    """

    run_id: str
    tenant_id: int | None
    prompt_key: str
    version: int | None
    source: str
    ts: datetime


def record(
    *,
    run_id: str,
    tenant_id: int | None,
    prompt_key: str,
    version: int | None,
) -> PromptRunRecord:
    """Note that ``run_id`` was served ``version`` of ``prompt_key``.

    Called once per run, at start, from the composition root's ``run_agent`` wrapper —
    the one place that knows the run id, the persona and the tenant together.

    Args:
        run_id: The run's id.
        tenant_id: The run's sealed tenant scope.
        prompt_key: The registry key resolved.
        version: The active version served, or ``None`` for the shipped floor prompt.

    Returns:
        The stored record.
    """
    entry = PromptRunRecord(
        run_id=run_id,
        tenant_id=tenant_id,
        prompt_key=prompt_key,
        version=version,
        source="registry" if version is not None else "floor",
        ts=datetime.now(UTC),
    )
    with _LOCK:
        _RUNS.append(entry)
    return entry


def resolve(run_id: str, tenant_id: int | None) -> PromptRunRecord | None:
    """Return what ``run_id`` was served, **within** ``tenant_id``, or ``None``.

    The tenant is a filter, not a hint: a run id that exists but belongs to another
    tenant returns ``None``, so a guessed or leaked id answers "not found" rather than
    naming another tenant's prompt.
    """
    with _LOCK:
        for entry in reversed(_RUNS):
            if entry.run_id == run_id and entry.tenant_id == tenant_id:
                return entry
    return None


def recent(tenant_id: int | None, *, limit: int = 25) -> list[PromptRunRecord]:
    """Return one tenant's most recent runs, newest first."""
    with _LOCK:
        rows = [entry for entry in reversed(_RUNS) if entry.tenant_id == tenant_id]
    return rows[: max(1, limit)]


def clear() -> None:
    """Drop every recorded run (test isolation)."""
    with _LOCK:
        _RUNS.clear()
