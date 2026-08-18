"""The stage contract — the resume arithmetic, the queue policy, and the handler registry.

Everything here is a pure function over a frozen tuple, which is the point of declaring
the contract in ``aegis`` rather than in the host's orchestrator decorators: the piece of
durable execution that is genuinely ours can be proved with no server, no worker and no
event loop.

Three things are checked in the only way that can fail:

1. **The resume arithmetic**, including the case that matters most — an unrecognised
   ``completed_stage``. Guessing there would either re-parse two hundred pages or skip
   work that never ran, so it raises, and the test asserts the exception.
2. **The concurrency design**, read off the declarations rather than restated: every
   CPU-bound stage really is on a queue whose worker has one slot, and the queue every
   stage names really is one a worker polls.
3. **The handler registry refuses to be helpful.** A stage with no handler raises rather
   than returning an empty result, because a stage that silently "succeeds" would advance
   ``completed_stage`` past work that never happened.

The last test reads this module's own source: the sandbox-safety claim in its docstring is
a claim about its import list, so it is checked against the import list.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from aegis.jobs.stages import (
    CPU_QUEUE,
    DEFAULT_QUEUE,
    INGEST_STAGES,
    IO_QUEUE,
    TASK_QUEUES,
    QueueSpec,
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


@pytest.fixture(autouse=True)
def _clean_registry():
    """Never let one test's handler satisfy another test's lookup."""
    clear_stage_handlers()
    yield
    clear_stage_handlers()


# ─────────────────────────────────────────────────────────────────────────────
# The pipeline as declared
# ─────────────────────────────────────────────────────────────────────────────


def test_the_ingest_pipeline_is_the_six_stages_in_order():
    assert stage_names() == ("parse", "chunk", "enrich", "embed", "index", "graph")


def test_the_cpu_bound_stages_are_the_ones_on_the_single_slot_queue():
    # The design claim, read off the declarations rather than restated: a Docling parse
    # peaks around 2.2 GB, so two at once take a 16 GB box down. ``graph`` is the other
    # CPU-and-RAM-bound stage.
    on_cpu = {stage.name for stage in INGEST_STAGES if stage.task_queue == CPU_QUEUE}
    assert on_cpu == {"parse", "graph"}
    assert queue_spec(CPU_QUEUE).max_concurrent_activities == 1


def test_the_billed_network_stage_runs_wide():
    assert stage_spec("embed").task_queue == IO_QUEUE
    assert queue_spec(IO_QUEUE).max_concurrent_activities > 1


def test_only_the_default_queue_runs_workflows():
    # A workflow task occupying the CPU queue's single slot could wait for a slot held by
    # its own activity.
    running = {spec.name for spec in TASK_QUEUES if spec.runs_workflows}
    assert running == {DEFAULT_QUEUE}


def test_every_stage_names_a_queue_a_worker_actually_polls():
    declared = {spec.name for spec in TASK_QUEUES}
    for stage in INGEST_STAGES:
        assert stage.task_queue in declared, (
            f"stage {stage.name!r} is scheduled onto {stage.task_queue!r}, which no "
            "worker polls: it would hang rather than fail"
        )


def test_the_expensive_stages_retry_less_than_the_flaky_cheap_one():
    # Retrying a 30-minute parse five times wastes half a day rediscovering that a
    # document is malformed; a rate-limited embed call routinely succeeds on attempt four.
    assert stage_spec("parse").max_attempts < stage_spec("embed").max_attempts


# ─────────────────────────────────────────────────────────────────────────────
# The resume arithmetic
# ─────────────────────────────────────────────────────────────────────────────


def test_nothing_completed_means_everything_runs():
    assert remaining_stages(None) == INGEST_STAGES


def test_a_resume_restarts_after_the_last_committed_stage():
    assert stage_names(remaining_stages("enrich")) == ("embed", "index", "graph")


def test_a_failure_at_the_last_stage_does_not_re_parse():
    # The whole reason stage progress is on the row: a graph failure must not re-run parse.
    assert "parse" not in stage_names(remaining_stages("index"))


def test_a_fully_ingested_document_has_nothing_left_to_run():
    assert remaining_stages("graph") == ()


def test_an_unrecognised_completed_stage_refuses_to_guess():
    with pytest.raises(UnknownStageError) as raised:
        remaining_stages("ocr")

    message = str(raised.value)
    assert "ocr" in message
    # The message must say what the pipeline *does* declare, or the reader has no way to
    # tell a renamed stage from a typo.
    assert "parse" in message


def test_stage_spec_refuses_an_unknown_name():
    with pytest.raises(UnknownStageError):
        stage_spec("ocr")


def test_queue_spec_refuses_an_unknown_queue():
    with pytest.raises(UnknownStageError):
        queue_spec("aegis-gpu")


# ─────────────────────────────────────────────────────────────────────────────
# Construction-time validation
# ─────────────────────────────────────────────────────────────────────────────


def test_a_stage_on_an_undeclared_queue_is_rejected_at_construction():
    with pytest.raises(ValueError, match="TASK_QUEUES"):
        StageSpec("ocr", timeout_seconds=60, max_attempts=1, task_queue="aegis-gpu")


def test_a_stage_that_can_never_run_is_rejected():
    with pytest.raises(ValueError, match="max_attempts"):
        StageSpec("ocr", timeout_seconds=60, max_attempts=0, task_queue=DEFAULT_QUEUE)


def test_a_stage_with_no_timeout_is_rejected():
    with pytest.raises(ValueError, match="timeout_seconds"):
        StageSpec("ocr", timeout_seconds=0, max_attempts=1, task_queue=DEFAULT_QUEUE)


def test_a_queue_with_no_slots_is_rejected():
    with pytest.raises(ValueError, match="max_concurrent_activities"):
        QueueSpec(
            name="aegis-gpu",
            max_concurrent_activities=0,
            runs_workflows=False,
            rationale="would poll forever",
        )


def test_a_pipeline_declaring_one_stage_twice_is_rejected():
    from aegis.jobs.stages import _reject_duplicate_names

    duplicated = (
        StageSpec("parse", timeout_seconds=60, max_attempts=1, task_queue=DEFAULT_QUEUE),
        StageSpec("chunk", timeout_seconds=60, max_attempts=1, task_queue=DEFAULT_QUEUE),
        StageSpec("parse", timeout_seconds=60, max_attempts=1, task_queue=DEFAULT_QUEUE),
    )
    with pytest.raises(ValueError, match="twice"):
        _reject_duplicate_names(duplicated)


# ─────────────────────────────────────────────────────────────────────────────
# The handler registry
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_registered_handler_is_the_one_returned():
    async def handler(session, *, tenant_id, document_id, stage):
        return {"page_count": 12}

    register_stage_handler("parse", handler)

    assert await stage_handler("parse")(None, tenant_id=1, document_id=1, stage="parse") == {
        "page_count": 12
    }


def test_an_unhandled_stage_raises_rather_than_returning_nothing():
    with pytest.raises(UnregisteredStageError) as raised:
        stage_handler("parse")

    assert "completed_stage" in str(raised.value), (
        "the message must say why an empty result is not an option here"
    )


def test_registering_under_a_typo_is_refused():
    async def handler(session, *, tenant_id, document_id, stage):
        return {}

    # Otherwise the typo never runs and the real stage stays unhandled: two silent
    # failures for the price of one.
    with pytest.raises(UnknownStageError):
        register_stage_handler("prase", handler)


def test_clearing_the_registry_really_clears_it():
    async def handler(session, *, tenant_id, document_id, stage):
        return {}

    register_stage_handler("parse", handler)
    clear_stage_handlers()

    with pytest.raises(UnregisteredStageError):
        stage_handler("parse")


# ─────────────────────────────────────────────────────────────────────────────
# The sandbox-safety claim, checked against the source
# ─────────────────────────────────────────────────────────────────────────────


def test_this_module_imports_nothing_a_workflow_sandbox_would_re_execute():
    """The stage contract is read from inside a workflow, so its imports are a contract.

    The docstring claims this module is stdlib-only. That claim decays the moment someone
    adds ``from sqlalchemy import ...`` for a convenience type, so it is checked here
    against the module's own AST rather than trusted.
    """
    import aegis.jobs.stages as module

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    # ``sqlalchemy`` appears only under ``if TYPE_CHECKING``, which never executes.
    stdlib = {"__future__", "collections", "dataclasses", "typing"}
    assert imported - stdlib - {"sqlalchemy"} == set(), (
        f"unexpected runtime imports in the stage contract: {sorted(imported - stdlib)}"
    )
    type_checking_only = {
        alias.name.split(".")[0]
        for block in ast.walk(tree)
        if isinstance(block, ast.If)
        and isinstance(block.test, ast.Name)
        and block.test.id == "TYPE_CHECKING"
        for node in ast.walk(block)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in [ast.alias(name=node.module)]
    }
    assert "sqlalchemy" in type_checking_only
