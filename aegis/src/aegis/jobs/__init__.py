"""Aegis jobs — the record layer for durable, tenant-owned background work.

Aegis has one pre-existing job pattern (``aegis.memory.consolidate``) and it is a
SELECT-then-guarded-UPDATE with no lease, no heartbeat and no reaper: a worker killed
mid-job strands its row in ``RUNNING`` forever, matched by no sweeper and retried by
nothing. This package is the substrate that replaces it, and ingestion — a multi-stage,
minutes-long, billed pipeline that cannot live inside an HTTP request — is its first real
consumer.

The division of labour is the point:

* **This package declares the contract and owns the record.** :class:`JobRun` and
  :class:`Document` are tenant-scoped rows under Row-Level Security, and they are what
  answers "what does this tenant have, how far did it get, and what did it cost" — with
  no orchestrator reachable.
* **The host runs the work.** The orchestrator client, the worker bootstrap and the
  reconciler sweep live in the composing application (``app.jobs``), because they need
  host configuration and a host session factory.

So **``aegis.jobs`` must not import an orchestrator SDK** (``temporalio`` in this
platform's host), directly or transitively. That is what keeps this package importable by
a consumer who orchestrates differently, and what makes a driver swap touch the runner
rather than the schema. Importing it pulls ``sqlalchemy`` (the ``aegis[data]`` extra) and
:mod:`aegis.governance.models`, whose ``tenants`` / ``users`` tables the job foreign keys
reference — and nothing else.
"""

from __future__ import annotations

from aegis.jobs.models import Document, JobRun, JobStatus

__all__ = ["Document", "JobRun", "JobStatus"]
