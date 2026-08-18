"""Aegis runs — the durable, tenant-scoped, replayable record of what an agent did.

Aegis already had three ways to watch a run and **none of them survived a restart**:
OpenTelemetry spans leave for a collector, Phoenix is an ephemeral deep-dive, and the
SSE stream is gone when the socket closes. Five later surfaces need the same durable
record — the harness, replay, per-agent inspection, audit depth and per-tenant LLMOps
evidence — so this package is that record, and deliberately not a fourth way to watch a
run: it stores the **same events** :mod:`aegis.agent.events` already emits.

Three modules, one idea:

* :mod:`aegis.runs.models` — ``run_events`` (append-only, partitioned by month) and the
  ``runs`` header.
* :mod:`aegis.runs.partitions` — the monthly partitions: created with the table, rolled
  forward on a schedule, and pruned by **dropping whole partitions** rather than
  deleting rows.
* :mod:`aegis.runs.record` — the writer, and the fold that makes the header a
  regenerable projection of the log rather than a second source of truth.

Importing this package pulls ``sqlalchemy`` (the ``aegis[data]`` extra) plus the
governance and jobs models its foreign keys reference — and nothing from any host
application, no orchestrator SDK and no web framework.
"""

from __future__ import annotations

from aegis.runs.models import RUN_EVENTS_TABLE, RUNS_TABLE, Run, RunEvent
from aegis.runs.partitions import (
    RunEventPartition,
    ensure_run_event_partitions,
    partition_name_for,
    prune_run_event_partitions,
    run_event_partition_statements,
    run_event_partitions,
)
from aegis.runs.record import (
    RunEventRecord,
    RunHeader,
    RunPartitionMissingError,
    apply_event,
    fold_events,
    load_run_events,
    load_run_header,
    read_run_header,
    rebuild_run_header,
    reconcile_run_header,
    record_events,
)

__all__ = [
    "RUNS_TABLE",
    "RUN_EVENTS_TABLE",
    "Run",
    "RunEvent",
    "RunEventPartition",
    "RunEventRecord",
    "RunHeader",
    "RunPartitionMissingError",
    "apply_event",
    "ensure_run_event_partitions",
    "fold_events",
    "load_run_events",
    "load_run_header",
    "partition_name_for",
    "prune_run_event_partitions",
    "read_run_header",
    "rebuild_run_header",
    "reconcile_run_header",
    "record_events",
    "run_event_partition_statements",
    "run_event_partitions",
]
