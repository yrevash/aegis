"""The stage contract — the portable subset of durable, multi-stage execution.

A durable-execution engine gives three things to a pipeline like ingestion: an ordered
set of named stages, per-stage retry and timeout policy, and "resume after the last
committed stage". **Only the last of those is genuinely engine-specific**; the first two
are facts about *our* pipeline that happen to be consumed by an engine. Declaring them
here rather than encoding them in the host's orchestrator decorators buys three things
that a Temporal-native declaration would not:

* the console, the health page and the docs read **one** source for "what are the stages
  and which queue does each run on", instead of three lists that drift;
* :func:`remaining_stages` — the actual resume arithmetic — is testable with no engine,
  no server and no event loop, which is why it is a pure function over a tuple;
* a future orchestrator swap touches the runner and nothing else. That is the whole
  reason ``aegis.jobs`` is forbidden from importing an orchestrator SDK.

**This module is stdlib-only, and that is load-bearing rather than tidy.** The host's
workflow sandbox re-imports the module that defines a workflow, and a workflow reads
:data:`INGEST_STAGES` to decide what to run next. Keeping this module free of
third-party imports — and of *any* import-time work beyond building frozen dataclasses —
is what makes it safe to read from inside a workflow at all. See
``docs/dev_new_docs_v2/phase-03``: the measured boundary is that the sandbox accepts
``time.time()`` at import and rejects ``asyncio.run()``, so the rule is "never run an
event loop at import", and the cheapest way to obey it is to import nothing that might.

(The *package* ``aegis.jobs`` re-exports the ORM models and therefore does pull
SQLAlchemy, so a workflow still imports from here inside
``workflow.unsafe.imports_passed_through()``. That shares the already-imported module
rather than paying for the ORM's import graph on every workflow task; what this module's
own discipline buys is that nothing in it can *misbehave* when it is re-executed.)

Two concurrency numbers, not one
--------------------------------

:class:`QueueSpec` exists because *per-stage* policy and *per-worker* policy are
different numbers and conflating them is a known way to lose a box. A Docling parse
peaks around 2.2 GB; two concurrently would exhaust a 16 GB machine. So ``parse`` and
``graph`` are pinned to :data:`CPU_QUEUE`, whose worker runs
``max_concurrent_activities=1``, while ``embed`` runs on :data:`IO_QUEUE`, which is
network-bound and runs wide. The queue carries the concurrency policy; the stage merely
names the queue it belongs on.

The stage handler registry
--------------------------

:func:`register_stage_handler` is how the *work* of a stage reaches the substrate. The
substrate owns the transaction, the tenant scope, the idempotency check and the
``completed_stage`` bump; the handler owns the domain work (parse this PDF, embed these
chunks) and returns the record-layer columns its work discovered. Splitting it there is
what lets Phase 4 add real Docling parsing without touching a line of orchestration, and
what lets this phase's tests drive the substrate with handlers that do real, observable
work rather than a mock of one.

There is deliberately **no default handler**. A stage with nothing registered raises
:class:`UnregisteredStageError` rather than returning an empty result, because a stage
that silently "succeeds" without doing anything would advance ``completed_stage`` past
work that never happened — a lie the resume logic would then faithfully honour.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only; keeps this module stdlib-only
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "CPU_QUEUE",
    "DEFAULT_QUEUE",
    "HEARTBEAT_TIMEOUT_FACTOR",
    "INGEST_STAGES",
    "IO_QUEUE",
    "TASK_QUEUES",
    "QueueSpec",
    "StageHandler",
    "StageSpec",
    "UnknownStageError",
    "UnregisteredStageError",
    "clear_stage_handlers",
    "queue_spec",
    "register_stage_handler",
    "remaining_stages",
    "stage_handler",
    "stage_names",
    "stage_spec",
]


class UnknownStageError(LookupError):
    """A stage name that no :class:`StageSpec` in the pipeline declares.

    Raised by :func:`remaining_stages` and :func:`stage_spec`. It is an error rather
    than a shrug because the caller is almost always a *resume*: a ``completed_stage``
    value read off a row, written by an older or newer build of this pipeline. Treating
    an unrecognised value as "nothing completed" would re-parse two hundred pages;
    treating it as "everything completed" would skip the work entirely. Both are worse
    than stopping and saying the stage set changed under a live row.
    """


class UnregisteredStageError(LookupError):
    """A stage was run with no handler registered for it.

    The substrate refuses to advance ``completed_stage`` for work it did not actually
    perform, so a missing handler fails the activity loudly instead of committing a
    stage that did nothing. See the module docstring.
    """


#: How many missed heartbeats mean "this attempt is gone".
#:
#: Three, because one is a scheduling hiccup and two is a slow garbage collection, while
#: three consecutive misses on a local RPC means the process is not there. It multiplies
#: :attr:`StageSpec.heartbeat_seconds` into the timeout the runner actually sets; the SDK
#: throttles outbound heartbeats to a fraction of that timeout on its own, so a short
#: interval costs nothing in traffic.
HEARTBEAT_TIMEOUT_FACTOR = 3

#: The CPU-bound queue. Its worker runs **one** activity at a time — see
#: :data:`TASK_QUEUES` for why that number is 1 and not "a small number".
CPU_QUEUE = "aegis-cpu"

#: The network-bound queue: embedding calls, gateway round-trips, anything whose cost is
#: latency rather than RAM. Runs wide.
IO_QUEUE = "aegis-io"

#: Everything else, including workflow tasks. Between the two extremes.
DEFAULT_QUEUE = "aegis-default"


@dataclass(frozen=True, slots=True)
class QueueSpec:
    """One task queue and the concurrency policy that rides on it.

    The policy lives on the *queue* rather than on the stage because that is the only
    place it can be enforced: a stage cannot know how many copies of itself are running
    on a box, whereas the worker polling a queue knows exactly, and refuses to pick up
    the next task until a slot frees. Pinning a stage to a queue is therefore how a
    stage acquires a concurrency limit at all.

    Attributes:
        name: The queue name, as both worker and client must spell it.
        max_concurrent_activities: How many activity tasks one worker process on this
            queue may execute at once. This is the *worker* number; the per-stage
            number is expressed by which queue a stage sits on.
        runs_workflows: Whether a worker on this queue also executes workflow tasks.
            Only :data:`DEFAULT_QUEUE` does: a workflow task is cheap, and letting one
            occupy a slot on the single-slot CPU queue would deadlock a workflow behind
            its own activity.
        rationale: Why this number, in one sentence. Carried as data because it is the
            first thing anyone asks when they see ``1`` and reach for ``4``.
    """

    name: str
    max_concurrent_activities: int
    runs_workflows: bool
    rationale: str

    def __post_init__(self) -> None:
        """Reject a queue that cannot run anything.

        Raises:
            ValueError: If ``max_concurrent_activities`` is not at least 1.
        """
        if self.max_concurrent_activities < 1:
            raise ValueError(
                f"queue {self.name!r} declares max_concurrent_activities="
                f"{self.max_concurrent_activities}; a queue with no slots polls forever "
                "and every task routed to it hangs with no error anywhere"
            )


#: Every queue the platform runs, and the concurrency policy of each.
#:
#: ``aegis-cpu`` is **one** slot, not a small number: a Docling parse of a large PDF
#: peaks around 2.2 GB resident, so two at once take a 16 GB machine past the point
#: where the OS starts killing processes — and the process it kills is not necessarily
#: the parse. One slot makes the parses queue instead of contend, which is the entire
#: reason ingestion is a durable job rather than a request.
TASK_QUEUES: tuple[QueueSpec, ...] = (
    QueueSpec(
        name=CPU_QUEUE,
        max_concurrent_activities=1,
        runs_workflows=False,
        rationale=(
            "Docling parses are CPU-bound and peak around 2.2 GB; two concurrently "
            "exhaust a 16 GB box, so they serialise here instead of contending."
        ),
    ),
    QueueSpec(
        name=IO_QUEUE,
        max_concurrent_activities=32,
        runs_workflows=False,
        rationale=(
            "Embedding and gateway calls are network-bound: the limit that matters is "
            "the provider's rate limit, not this machine's RAM."
        ),
    ),
    QueueSpec(
        name=DEFAULT_QUEUE,
        max_concurrent_activities=8,
        runs_workflows=True,
        rationale=(
            "Short record-layer work plus the workflow tasks themselves, which must "
            "never queue behind a long activity."
        ),
    ),
)

_QUEUES_BY_NAME: dict[str, QueueSpec] = {spec.name: spec for spec in TASK_QUEUES}


@dataclass(frozen=True, slots=True)
class StageSpec:
    """One stage of a multi-stage job — the portable subset of durable execution.

    Stage names, their order, and "resume after the last committed stage" are exactly
    what a durable-execution engine provides. Declaring them here rather than encoding
    them in an engine's decorators means the console, the health page and the docs read
    one source, and a future orchestrator swap touches only the runner.

    Attributes:
        name: The stage's identity. This string is written to
            :attr:`aegis.jobs.Document.completed_stage`, so **renaming a stage is a data
            migration**, not a refactor: live rows carry the old name and
            :func:`remaining_stages` will refuse them.
        timeout_seconds: Wall-clock ceiling for one attempt. Sized to the work, not to a
            uniform default — a 200-page parse legitimately takes minutes, while a chunk
            that runs for five is wedged.
        max_attempts: Total attempts including the first. Low for the expensive stages
            (retrying a 30-minute parse five times wastes half a day discovering the
            document is malformed) and high for the flaky-but-cheap ones (``embed`` hits
            a rate-limited network API where attempt four routinely succeeds).
        task_queue: The queue this stage runs on, which is where its concurrency policy
            lives. See :class:`QueueSpec`.
        heartbeat_seconds: How often a running attempt reports that it is still alive.
            This is what makes a **hard-killed worker** recoverable in seconds rather
            than in ``timeout_seconds``: the orchestrator learns nothing from a
            ``SIGKILL``, so without a heartbeat an interrupted ``parse`` would sit
            "running" for the full half-hour before anything retried it — and the phase's
            own demo is killing a worker mid-ingest and watching the job get reclaimed.
            The runner derives the *timeout* from this by
            :data:`HEARTBEAT_TIMEOUT_FACTOR`, so the number here is the interval, not the
            deadline.
    """

    name: str
    timeout_seconds: int
    max_attempts: int
    task_queue: str
    heartbeat_seconds: int = 5

    def __post_init__(self) -> None:
        """Validate the stage at construction, where the fix is cheap.

        Raises:
            ValueError: If the name is blank, a timeout or attempt count is not
                positive, or the stage names a queue that :data:`TASK_QUEUES` does not
                declare. The last one matters most: a stage on an unknown queue is
                scheduled onto a queue nothing polls, so it never runs and never errors.
        """
        if not self.name.strip():
            raise ValueError("a stage must have a name: it is written to completed_stage")
        if self.timeout_seconds <= 0:
            raise ValueError(f"stage {self.name!r}: timeout_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError(
                f"stage {self.name!r}: max_attempts must be at least 1, else the stage "
                "can never run at all"
            )
        if self.heartbeat_seconds <= 0:
            raise ValueError(
                f"stage {self.name!r}: heartbeat_seconds must be positive, or a killed "
                "worker leaves this stage unrecoverable until timeout_seconds expires"
            )
        if self.heartbeat_timeout_seconds >= self.timeout_seconds:
            raise ValueError(
                f"stage {self.name!r}: the derived heartbeat timeout "
                f"({self.heartbeat_timeout_seconds}s) is not shorter than "
                f"timeout_seconds ({self.timeout_seconds}s), so the heartbeat could "
                "never detect a dead worker sooner than the attempt timeout already does"
            )
        if self.task_queue not in _QUEUES_BY_NAME:
            raise ValueError(
                f"stage {self.name!r} names task queue {self.task_queue!r}, which is not "
                f"in TASK_QUEUES ({sorted(_QUEUES_BY_NAME)}); no worker would poll it, so "
                "the stage would hang rather than fail"
            )

    @property
    def queue(self) -> QueueSpec:
        """The concurrency policy this stage inherits from its queue."""
        return _QUEUES_BY_NAME[self.task_queue]

    @property
    def heartbeat_timeout_seconds(self) -> int:
        """How long a silent attempt is tolerated before it is treated as dead.

        Derived rather than declared so the two numbers cannot drift apart: a heartbeat
        interval longer than its own timeout would fail every healthy attempt, which is
        a spectacular way to break a pipeline with a one-character edit.
        """
        return self.heartbeat_seconds * HEARTBEAT_TIMEOUT_FACTOR


#: The ingestion pipeline, in order. This is the tuple a resume walks.
#:
#: The queue assignment is the design, not a detail: ``parse`` and ``graph`` are the two
#: CPU-and-RAM-bound stages and therefore serialise on :data:`CPU_QUEUE`, ``embed`` is
#: the billed network stage and runs wide on :data:`IO_QUEUE`, and the three cheap
#: record-layer stages sit on :data:`DEFAULT_QUEUE`.
INGEST_STAGES: tuple[StageSpec, ...] = (
    StageSpec("parse", timeout_seconds=1800, max_attempts=2, task_queue=CPU_QUEUE),
    StageSpec("chunk", timeout_seconds=300, max_attempts=3, task_queue=DEFAULT_QUEUE),
    StageSpec("enrich", timeout_seconds=300, max_attempts=3, task_queue=DEFAULT_QUEUE),
    StageSpec("embed", timeout_seconds=900, max_attempts=5, task_queue=IO_QUEUE),
    StageSpec("index", timeout_seconds=600, max_attempts=3, task_queue=DEFAULT_QUEUE),
    StageSpec("graph", timeout_seconds=1800, max_attempts=2, task_queue=CPU_QUEUE),
)


def _reject_duplicate_names(stages: tuple[StageSpec, ...]) -> None:
    """Fail at import if a pipeline declares one stage name twice.

    Args:
        stages: The pipeline to check.

    Raises:
        ValueError: If any name repeats. Duplicates are not a style problem: the resume
            arithmetic in :func:`remaining_stages` finds the *first* match, so a
            duplicate silently makes everything between the two copies re-run on every
            resume.
    """
    seen: set[str] = set()
    for stage in stages:
        if stage.name in seen:
            raise ValueError(
                f"stage {stage.name!r} is declared twice; a resume would re-run every "
                "stage between the two declarations"
            )
        seen.add(stage.name)


_reject_duplicate_names(INGEST_STAGES)


def queue_spec(name: str) -> QueueSpec:
    """Return the concurrency policy for one queue name.

    Args:
        name: A queue name, e.g. :data:`CPU_QUEUE`.

    Returns:
        The matching :class:`QueueSpec`.

    Raises:
        UnknownStageError: If no queue by that name is declared. (The lookup error is
            shared with stages deliberately: both mean "this pipeline does not know that
            name", and a caller that wants to catch one wants to catch the other.)
    """
    try:
        return _QUEUES_BY_NAME[name]
    except KeyError as exc:
        raise UnknownStageError(
            f"unknown task queue {name!r}; declared queues are {sorted(_QUEUES_BY_NAME)}"
        ) from exc


def stage_names(stages: tuple[StageSpec, ...] = INGEST_STAGES) -> tuple[str, ...]:
    """Return the stage names of a pipeline, in order.

    Args:
        stages: The pipeline. Defaults to :data:`INGEST_STAGES`.

    Returns:
        The ordered names, for rendering a progress bar or parametrising a test.
    """
    return tuple(stage.name for stage in stages)


def stage_spec(name: str, stages: tuple[StageSpec, ...] = INGEST_STAGES) -> StageSpec:
    """Return one stage's spec by name.

    Args:
        name: The stage name.
        stages: The pipeline to look in. Defaults to :data:`INGEST_STAGES`.

    Returns:
        The matching :class:`StageSpec`.

    Raises:
        UnknownStageError: If the pipeline declares no such stage.
    """
    for stage in stages:
        if stage.name == name:
            return stage
    raise UnknownStageError(
        f"unknown stage {name!r}; {stage_names(stages)} are the stages this pipeline "
        "declares"
    )


def remaining_stages(
    completed_stage: str | None, stages: tuple[StageSpec, ...] = INGEST_STAGES
) -> tuple[StageSpec, ...]:
    """Return the stages still to run after ``completed_stage`` — the resume arithmetic.

    This is the one piece of durable-execution semantics that is genuinely ours rather
    than the engine's, and it is a pure function so it can be proved without a server.
    ``None`` means nothing has committed yet, so everything runs; a name means the run
    resumes *after* that stage, which is what stops a failure in ``graph`` re-parsing two
    hundred pages.

    Args:
        completed_stage: The last stage that committed, read off the row, or ``None``.
        stages: The pipeline. Defaults to :data:`INGEST_STAGES`.

    Returns:
        The stages after ``completed_stage``, in order. Empty when the last stage has
        already committed — which callers should treat as "this job is done", not as an
        error.

    Raises:
        UnknownStageError: If ``completed_stage`` names no stage in the pipeline. See
            that exception for why guessing is worse than failing.
    """
    if completed_stage is None:
        return stages
    for index, stage in enumerate(stages):
        if stage.name == completed_stage:
            return stages[index + 1 :]
    raise UnknownStageError(
        f"row reports completed_stage={completed_stage!r}, which is not one of "
        f"{stage_names(stages)}. The stage set changed under a live row: resuming would "
        "either redo committed work or skip work that never ran, so neither is guessed."
    )


class StageHandler(Protocol):
    """The domain work of one stage, as the substrate calls it.

    The substrate has already opened a session, bound the tenant scope, and checked that
    this stage has not already committed. The handler does the real work and returns the
    record-layer columns it discovered; the substrate applies them **and** the
    ``completed_stage`` bump in the same transaction it handed in, so a stage that
    "finished" but whose output rolled back cannot exist.

    A handler that also writes rows of its own (chunks, embeddings) must write them on
    the session it is given, for the same reason: a second session is a second
    transaction, and a second transaction is where the stage commit rule dies.
    """

    async def __call__(
        self, session: AsyncSession, *, tenant_id: int | None, document_id: int, stage: str
    ) -> Mapping[str, Any]:
        """Perform the stage and return the document columns it determined.

        Args:
            session: The scoped session, inside the stage's single transaction.
            tenant_id: The owning tenant, already bound as the session's scope.
            document_id: The document being processed.
            stage: The stage name, so one handler can serve several stages.

        Returns:
            A mapping of :class:`aegis.jobs.Document` column names to values, applied by
            the substrate alongside the ``completed_stage`` bump. Empty is legitimate
            for a stage whose output lives entirely in other tables.
        """
        ...


_HANDLERS: dict[str, StageHandler] = {}


def register_stage_handler(stage: str, handler: StageHandler) -> None:
    """Register the work that one stage performs.

    Args:
        stage: The stage name, which must be one this pipeline declares.
        handler: The coroutine function implementing it.

    Raises:
        UnknownStageError: If ``stage`` is not a declared stage name. Registering under
            a typo would leave the real stage unhandled, and the typo would never run —
            two silent failures for the price of one.
    """
    stage_spec(stage)
    _HANDLERS[stage] = handler


def stage_handler(stage: str) -> StageHandler:
    """Return the handler registered for a stage.

    Args:
        stage: The stage name.

    Returns:
        The registered :class:`StageHandler`.

    Raises:
        UnregisteredStageError: If nothing is registered. Deliberately not a no-op — see
            the module docstring.
    """
    try:
        return _HANDLERS[stage]
    except KeyError as exc:
        raise UnregisteredStageError(
            f"no handler registered for stage {stage!r}; registered stages are "
            f"{sorted(_HANDLERS)}. The substrate will not advance completed_stage past "
            "work it did not perform."
        ) from exc


def clear_stage_handlers() -> None:
    """Drop every registered handler.

    Exists for tests, which register real handlers per case and must not leak them into
    the next one. Named plainly rather than underscore-private because a host that
    rebuilds its wiring (a reload, a second worker bootstrap in one process) needs it
    too, and would otherwise reach for the private dict.
    """
    _HANDLERS.clear()


#: The callable shape :func:`register_stage_handler` accepts, spelled out for callers
#: who prefer an alias to a Protocol in an annotation.
StageHandlerFn = Callable[..., Awaitable[Mapping[str, Any]]]
