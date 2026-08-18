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
  no orchestrator reachable. :mod:`aegis.jobs.stages` declares the stages, their retry
  and timeout policy, the queues that carry the concurrency policy, and the resume
  arithmetic; :mod:`aegis.jobs.scope` declares the one rule every activity obeys —
  :func:`~aegis.jobs.scope.tenant_activity` binds the tenant scope from the activity's
  own typed argument and refuses to run without one.
* **It also owns tenant policy, which is not execution mechanics.**
  :mod:`aegis.jobs.admission` decides whether a tenant may start another job at all —
  the concurrency cap and the budget pre-authorisation, both read from the settings
  catalogue — and :mod:`aegis.jobs.cancel` decides whether this caller may stop this row
  and records who did. Neither answer belongs in a workflow definition, and both must be
  answerable with no orchestrator running.
* **The host runs the work.** The orchestrator client, the worker bootstrap and the
  reconciler sweep live in the composing application (``app.jobs``), because they need
  host configuration and a host session factory.

So **``aegis.jobs`` must not import an orchestrator SDK** (``temporalio`` in this
platform's host), directly or transitively. That is what keeps this package importable by
a consumer who orchestrates differently, and what makes a driver swap touch the runner
rather than the schema. Importing it pulls ``sqlalchemy`` (the ``aegis[data]`` extra) and
the ``aegis[governance]`` extra — :mod:`aegis.governance.models`, whose ``tenants`` /
``users`` tables the job foreign keys reference, plus the budgets and settings that
admission reads — and nothing else. In particular no orchestrator SDK, no web framework
and no model client.
"""

from __future__ import annotations

from aegis.jobs.admission import (
    IN_FLIGHT_STATUSES,
    AdmissionDeniedError,
    AdmissionError,
    BudgetExceededError,
    admit,
    max_inflight_key,
)
from aegis.jobs.cancel import (
    TERMINAL_STATUSES,
    CancellationError,
    JobNotCancellableError,
    JobNotVisibleError,
    cancel_job,
)
from aegis.jobs.models import Document, JobRun, JobStatus
from aegis.jobs.scope import (
    ActivityInput,
    MissingTenantScopeError,
    SessionFactoryNotConfiguredError,
    activity_session_factory,
    reset_activity_session_factory,
    set_activity_session_factory,
    tenant_activity,
)
from aegis.jobs.stages import (
    CPU_QUEUE,
    DEFAULT_QUEUE,
    INGEST_STAGES,
    IO_QUEUE,
    TASK_QUEUES,
    QueueSpec,
    StageHandler,
    StageSpec,
    UnknownStageError,
    UnregisteredStageError,
    clear_stage_handlers,
    queue_spec,
    register_stage_handler,
    remaining_stages,
    stage_handler,
    stage_names,
    stage_spec,
)

__all__ = [
    "CPU_QUEUE",
    "DEFAULT_QUEUE",
    "INGEST_STAGES",
    "IN_FLIGHT_STATUSES",
    "IO_QUEUE",
    "TASK_QUEUES",
    "TERMINAL_STATUSES",
    "ActivityInput",
    "AdmissionDeniedError",
    "AdmissionError",
    "BudgetExceededError",
    "CancellationError",
    "Document",
    "JobNotCancellableError",
    "JobNotVisibleError",
    "JobRun",
    "JobStatus",
    "MissingTenantScopeError",
    "QueueSpec",
    "SessionFactoryNotConfiguredError",
    "StageHandler",
    "StageSpec",
    "UnknownStageError",
    "UnregisteredStageError",
    "activity_session_factory",
    "admit",
    "cancel_job",
    "clear_stage_handlers",
    "max_inflight_key",
    "queue_spec",
    "register_stage_handler",
    "remaining_stages",
    "reset_activity_session_factory",
    "set_activity_session_factory",
    "stage_handler",
    "stage_names",
    "stage_spec",
    "tenant_activity",
]
