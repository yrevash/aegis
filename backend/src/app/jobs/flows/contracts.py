"""The typed arguments that cross the workflow/activity boundary.

Every one of these is a frozen dataclass, and every one that an activity receives carries
``tenant_id`` — which is not a convention here but the mechanism:
:func:`aegis.jobs.scope.tenant_activity` reads the tenant off this argument and refuses to
run without it. Putting the field on the argument rather than in ambient context is what
makes the scope survive a replay in a fresh worker process, because the argument is
serialised into the orchestrator's history and the context is not.

They live in their own module, separate from :mod:`app.jobs.activities`, so a workflow
can import the shapes it needs without importing the activity *implementations* — and
therefore without dragging the session factory, the ORM and the application settings into
the workflow sandbox's import graph.

Frozen and ``slots``-based for the reasons given on
:class:`aegis.jobs.scope.ActivityInput`: a replayed activity must be handed exactly the
value the original run was handed, and a mutable argument is the one way that stops being
true.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aegis.jobs.scope import ActivityInput

__all__ = [
    "FINISH_INGEST",
    "RUN_STAGE",
    "START_INGEST",
    "FinishInput",
    "IngestParams",
    "IngestResult",
    "StageInput",
    "StageOutcome",
    "StartOutcome",
]


#: Activity names, as the worker registration, the workflow's call site and every test
#: spell them. They live here rather than beside the implementations because a workflow
#: invokes an activity **by string name** whenever it routes it to another task queue —
#: so the name is part of the wire contract, and a mismatch between the two spellings does
#: not fail at import. It fails at run time, as a task nothing ever polls for.
START_INGEST = "aegis_start_ingest"
RUN_STAGE = "aegis_run_stage"
FINISH_INGEST = "aegis_finish_ingest"


@dataclass(frozen=True, slots=True)
class IngestParams:
    """The workflow's own argument: which tenant's document to ingest.

    Not an :class:`~aegis.jobs.scope.ActivityInput` — a workflow binds no database scope
    and touches no session; it decides *what* runs and delegates every read and write to
    activities. Giving it a session-shaped argument would invite the opposite.

    Attributes:
        tenant_id: The owning tenant, threaded onto every activity argument the workflow
            builds. ``None`` is legitimate only for platform-level ingestion and is
            refused by every activity that has not opted in.
        document_id: The :class:`aegis.jobs.Document` row to ingest.
        user_id: Who uploaded it, recorded on the job row so a cost can be attributed to
            a person and not only to a tenant.
    """

    tenant_id: int | None
    document_id: int
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class StartOutcome:
    """What the claim activity found, and therefore where the run must start.

    Attributes:
        document_id: Echoed back so a caller reading only the outcome has the key.
        completed_stage: The last stage that committed for this document, or ``None``.
            This is the value the workflow feeds to
            :func:`aegis.jobs.stages.remaining_stages`, and it is why a re-run of a
            document that already parsed does not re-parse it.
        job_run_id: The ``job_runs`` row backing this execution.
    """

    document_id: int
    completed_stage: str | None
    job_run_id: int


@dataclass(frozen=True, slots=True)
class StageInput(ActivityInput):
    """One stage of one document — the stage runner's argument.

    Attributes:
        document_id: The document being processed.
        stage: The stage name, which must be one :data:`aegis.jobs.INGEST_STAGES`
            declares. The activity re-validates it rather than trusting the caller: a
            stage name is written to ``completed_stage``, and an unrecognised value there
            would break every future resume of this row.
    """

    document_id: int
    stage: str


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """The result of running one stage.

    Attributes:
        stage: The stage that ran.
        document_id: The document it ran for.
        committed: ``True`` when this call performed the work and advanced
            ``completed_stage``; ``False`` when it found the stage already committed and
            did nothing. The distinction is the idempotency evidence: a replayed activity
            returns ``False``, and a test can assert that rather than infer it.
    """

    stage: str
    document_id: int
    committed: bool


@dataclass(frozen=True, slots=True)
class FinishInput(ActivityInput):
    """Close out a run, successfully or not.

    One activity for both outcomes rather than two, because the two write the same
    columns on the same two rows and differ only in the value of ``status`` — and a
    second activity would be a second place for the "and set ``finished_at``" step to be
    forgotten.

    Attributes:
        document_id: The document whose run is ending.
        status: The terminal :class:`aegis.jobs.JobStatus` value, as its string.
        error: The failure reason, or ``None`` on success. Recorded on the row because a
            failed job whose reason lives only in the orchestrator's UI is a job the
            tenant cannot be told anything about.
    """

    document_id: int
    status: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What the workflow returns.

    Attributes:
        document_id: The document ingested.
        stages_run: The stages this execution actually ran, in order. Empty when the
            document was already fully ingested — which is a legitimate, successful
            outcome, not an error.
    """

    document_id: int
    stages_run: tuple[str, ...] = field(default_factory=tuple)
