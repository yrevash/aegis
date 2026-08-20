"""``python -m app.demo`` — ninety days of history, and one command that removes it.

``app.seed`` writes the *starting state*: principals, tenants, budgets, documents,
three parked gates. It deliberately writes no **history**, and history is what five of
the console's screens are made of. Measured against the live database on 2026-08-20,
``usage_ledger`` held 99 rows spanning **two** calendar days, ``runs`` held one, and
``job_runs`` held two — so:

* ``GET /forecast/*`` refused with *"2 of 71 observations needed"*. That is arithmetic,
  not a bug: :func:`aegis.forecast.minimum_history` computes ``3*14 + max(2*14+1, 15,
  28) = 71`` for the default ``horizon=14, freq='D'``, and the ledger held two buckets.
* Overview degraded to six dashed boxes, and Analytics / Audit / Jobs to empty tables.

This module writes that history. It is **not** a second seeder: it is a demo corpus,
and the two are different kinds of thing. ``app.seed`` writes rows a deployment keeps;
this writes rows a deployment must be able to *delete without residue* the moment real
data arrives.

The tag, and why it is where it is
----------------------------------

Every row written here carries the marker :data:`DEMO_PREFIX` on the table's own
**correlation identity** — the string column each of these tables already has and
already shows on screen:

============== ======================= ==========================================
table          tagged column           example
============== ======================= ==========================================
usage_ledger   ``trace_id``            ``demo-t3f2a91c04d7``
audit_log      ``trace_id``            ``demo-t3f2a91c04d7``
runs           ``run_id``              ``demo-run-000417``
run_events     ``run_id``              ``demo-run-000417``
job_runs       ``workflow_id``         ``demo-job-000031``
approvals      ``id``                  ``demo-gate-000012``
redteam_runs   ``run_id``              ``demo-rt-000002``
============== ======================= ==========================================

Three reasons this is the honest place rather than a new column:

1. **It is not a repurposing.** A demo ledger row genuinely belongs to the demo run
   whose trace id it carries — the run header, its events, its ledger rows and its
   audit entries all share one id, so the correlation the column promises is real and
   navigable. The prefix adds provenance to a true value; it does not overload a field
   with a second meaning.
2. **It cannot collide.** A real ``trace_id`` here is a 32-character lowercase-hex OTel
   id (:func:`aegis.observability.current_trace_id`), a real ``runs.run_id`` is a
   uuid/``ingest:<tenant>:<doc>`` form, a real ``redteam_runs.run_id`` is ``rt-<hex>``,
   and a real ``workflow_id`` is ``ingest:…``/``reindex:…``. None can begin ``demo-``,
   so ``LIKE 'demo-%'`` selects the corpus and nothing else — and every one of those
   columns is already indexed, so the wipe is a seek, not a scan.
3. **It is visible.** The prefix shows up next to the cost on the spend surfaces and
   next to the action on the audit trail. A synthetic $4.10 that says ``demo-`` on the
   same row cannot be mistaken for metered spend by anyone reading the screen — which
   is the property a fabricated financial record actually needs.

``--wipe`` is therefore exact by construction: one indexed predicate per table, and a
count of what each one deleted.

What it writes
--------------

``usage_ledger`` is the important one, because it is the series the forecast reads and
the money every spend surface shows. The shape is deliberate:

* a **weekday/weekend rhythm** (:data:`_WEEKDAY_FACTOR`), so the ``D``-frequency
  seasonality the forecaster assumes (``season_length=7``) is actually there;
* a mild **upward trend** across the window, so the projection has a slope to project;
* **two deliberate spikes** (:data:`_SPIKES`), so the conformal band has residuals to
  calibrate on and the burn-down draws something other than a straight line;
* a believable **model mix** drawn from :data:`aegis.gateway.routing._FLEET_DECLARATION`
  through :func:`~aegis.gateway.routing.allowed_deployments`, with every row's
  ``cost_usd`` computed by :func:`~aegis.gateway.routing.unit_cost` from that
  deployment's own declared rates — including the non-token units, so the whisper rows
  carry ``audio_seconds`` and the vision rows carry ``images`` and both are priced the
  way the gateway would have priced them;
* split across both seeded tenants **and** a platform-scoped (NULL-tenant) slice, since
  the platform's own spend is ledgered under a NULL tenant by design.

``runs`` is written **through its events**, never as a bare header:
:func:`aegis.runs.record.fold_events` computes each ``runs`` row from the
``run_events`` rows stored beside it, so the projection this corpus writes is
reproducible from its own log by :func:`~aegis.runs.record.rebuild_run_header`. A
header nothing emitted would be a row that disagrees with the table it summarises.

``job_runs``, ``audit_log`` and the ``approvals`` history hang off the same runs, and
the ``redteam_runs`` rows are **real offline battery runs** — :func:`aegis.redteam.
runner.run_redteam` with no completer drives the deterministic backstops only, so the
stored report is a genuine set of verdicts rather than a fabricated one.

What this corpus is **not** made of
-----------------------------------

The adapter's record generator is deliberately not read here, and that is a correction
rather than an omission. Everything in these six tables is *platform* telemetry — model
spend, run headers, job outcomes, the governance trail — and none of it is domain
content. The first draft of this module did reach into the generator for decoration
(a record id in a tool argument, a category in an audit payload) and
``tests/adapter/test_conformance_suite.py`` refused it, correctly: a core module that
spells the shipped domain's record type or its collection's field name is a retarget
that looks finished and serves the previous domain's words over the new domain's data.

The one place domain content genuinely belongs is the call a human gate authorises, and
that is taken through the seam without naming it: the tool is selected from
:data:`app.adapter.TOOL_REGISTRY` **by its declared risk tier** (the highest-risk tool is
the one that raises a gate, which is the platform's own rule and not this domain's), and
its arguments are built from that tool's own ``args_model`` — so on swap day the demo
gates carry the new domain's tool, with the new domain's argument names, having changed
nothing here.

Rules this module holds itself to
---------------------------------

* **The gateway is never called.** The Azure fleet is live and bills real money. Ledger
  rows are computed from the fleet's declared rates and written directly.
* **Every write binds a tenant scope**, so the ``tenant_isolation`` policies apply to
  the seeder exactly as they do to a request, and a tenant cannot end up holding
  another's rows.
* **Idempotent per table.** Each writer asks whether its table already holds demo rows
  and writes nothing when it does, so a second run creates nothing — exactly as
  ``app.seed`` behaves.
* **Gated.** Seeding requires ``AEGIS_DEMO_DATA=1``. ``--wipe`` deliberately does
  **not** require it: the removal path must never be the thing that fails because a
  variable was not exported.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, get_args

from aegis.core.models import ModelRole
from aegis.core.types import RiskLevel, RunStatus
from aegis.gateway.routing import allowed_deployments, unit_cost
from aegis.governance.models import AuditLog, UsageLedger
from aegis.governance.types import Role
from aegis.jobs.models import JobRun, JobStatus
from aegis.redteam.models import RedTeamRun
from aegis.runs.models import Run, RunEvent
from aegis.runs.partitions import ensure_run_event_partitions
from aegis.runs.record import RunEventRecord, fold_events
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import SQLAlchemyError

from app.adapter import TOOL_REGISTRY, agent_roster, persona_for_role
from app.data import (
    Approval,
    ApprovalStatus,
    Tenant,
    User,
    get_admin_engine,
    get_sessionmaker,
    set_tenant_scope,
)
from app.seed import TENANTS

__all__ = [
    "DEMO_ENV",
    "DEMO_HISTORY_DAYS",
    "DEMO_PREFIX",
    "DEMO_RNG_SEED",
    "DemoCorpus",
    "DemoScope",
    "DemoSummary",
    "TAGGED_COLUMN",
    "WipeSummary",
    "build_corpus",
    "demo_enabled",
    "main",
    "resolve_scopes",
    "seed_demo",
    "wipe_demo",
]

logger = logging.getLogger(__name__)

#: The environment variable that must be ``1`` before anything is written.
DEMO_ENV = "AEGIS_DEMO_DATA"

#: The marker every demo row carries, on the column named in :data:`TAGGED_COLUMN`.
#: Reserved: nothing else in this system may mint an identifier that starts with it.
DEMO_PREFIX = "demo-"

#: Table → the column carrying :data:`DEMO_PREFIX`. This mapping **is** the kill
#: switch: the wipe deletes ``WHERE <column> LIKE 'demo-%'`` from each of these and
#: touches nothing else, in an order that respects the one foreign key between them
#: (``run_events.job_id`` → ``job_runs.id``).
TAGGED_COLUMN: tuple[tuple[Any, Any], ...] = (
    (RunEvent, RunEvent.run_id),
    (Run, Run.run_id),
    (UsageLedger, UsageLedger.trace_id),
    (AuditLog, AuditLog.trace_id),
    (Approval, Approval.id),
    (JobRun, JobRun.workflow_id),
)

#: Days of history the corpus spans, ending today. ``minimum_history(14, 7)`` is 71 for
#: the default horizon, so ninety leaves nineteen buckets of headroom — enough that the
#: forecast still fits after the console's own lookback trims the oldest edge.
DEMO_HISTORY_DAYS = 90

#: The RNG seed. Fixed, so two runs of this module produce the identical corpus and a
#: screenshot taken today matches the database rebuilt tomorrow.
DEMO_RNG_SEED = 20260830

#: Multiplier on a day's volume, Monday..Sunday. The weekend trough is what gives the
#: daily series the ``season_length=7`` cycle the forecaster looks for; a flat series
#: fits a straight line and says nothing.
_WEEKDAY_FACTOR: tuple[float, ...] = (1.00, 1.06, 1.08, 1.02, 0.90, 0.30, 0.19)

#: Day index (0 = the oldest day) → volume multiplier. Two deliberate incidents: a
#: three-day surge around a quarter-end reconciliation and a two-day one after a
#: release. They exist so the conformal band has real residuals to calibrate on.
_SPIKES: dict[int, float] = {33: 2.9, 34: 3.4, 35: 2.1, 66: 2.2, 67: 2.7}

#: Agent runs per day across every scope, before the factors above are applied.
_RUNS_PER_DAY = 30

#: The heavy (answer-generating) deployments a run may use, and how often. Weighted
#: towards the fleet's cheap generation models, as a cost-aware platform would be.
_HEAVY_MIX: tuple[tuple[str, int], ...] = (
    ("genailab-maas-gpt-4o", 5),
    ("genailab-maas-DeepSeek-V3-0324", 4),
    ("genailab-maas-Llama-3.3-70B-Instruct", 3),
    ("genailab-maas-Llama-4-Maverick-17B-128E-Instruct-FP8", 3),
    ("genailab-maas-Phi-4-reasoning", 2),
    ("genailab-maas-DeepSeek-R1", 1),
)

#: The classifier deployments the guardrail rails call on every turn. Reserved to the
#: platform's own safety layers (§7.16 row 7), which is exactly why they appear on
#: every run regardless of which tenant raised it.
_GUARD_MIX: tuple[tuple[str, int], ...] = (
    ("genailab-maas-gpt-4o-mini", 7),
    ("genailab-maas-gpt-35-turbo", 3),
)

#: The vision deployments, used by the runs that screened an attached image.
_VISION_MIX: tuple[tuple[str, int], ...] = (
    ("genailab-maas-Phi-3.5-vision-instruct", 3),
    ("genailab-maas-Llama-3.2-90B-Vision-Instruct", 1),
)

#: The single embedding and voice deployments.
_EMBEDDING_DEPLOYMENT = "genailab-maas-text-embedding-3-large"
_VOICE_DEPLOYMENT = "genailab-maas-whisper"

#: The graph nodes a completed run walks, with the labels the console renders. These are
#: :data:`aegis.agent.graph.NODE_LABELS` — core plumbing, not domain vocabulary.
_RUN_NODES: tuple[tuple[str, str], ...] = (
    ("guard_input", "Input guardrail"),
    ("route", "Route intent"),
    ("retrieve", "Agentic retrieval"),
    ("plan", "Reason & plan"),
    ("generate", "Generate answer"),
    ("guard_output", "Output guardrail"),
)

#: Job types this platform actually runs (``app.ingestion.upload``, ``app.jobs.reindex``).
_JOB_TYPES: tuple[tuple[str, int], ...] = (("ingest", 7), ("reindex", 2), ("eval", 1))

#: The SLA window a still-pending demo gate is given. Thirty days, matching
#: ``app.seed``: the SLA sweeper is real and it runs, and a gate seeded with the
#: configured hour-long default would be auto-rejected before anyone saw the inbox.
_DEMO_APPROVAL_SLA = timedelta(days=30)

#: The suites the corpus runs the offline battery for, and the scope each is run under.
_REDTEAM_SUITES: tuple[str, ...] = ("owasp-full", "prompt-injection")


def demo_enabled() -> bool:
    """Return whether ``AEGIS_DEMO_DATA`` authorises writing the demo corpus."""
    return os.environ.get(DEMO_ENV, "").strip() == "1"


def _tag(kind: str, index: int) -> str:
    """Return the tagged identifier for the ``index``-th row of ``kind``."""
    return f"{DEMO_PREFIX}{kind}-{index:06d}"


def _trace(rng: random.Random) -> str:
    """Return a tagged trace id: the prefix plus twelve hex characters."""
    return f"{DEMO_PREFIX}t{rng.getrandbits(48):012x}"


# ─────────────────────────────────────────────────────────────────────────────
# Scopes — which tenants and principals the corpus is attributed to
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DemoScope:
    """One attribution scope: a tenant (or the platform) and the principals in it.

    Attributes:
        label: Human name, used in the summary and in generated prose.
        tenant_id: The owning tenant, or ``None`` for the platform slice. A NULL
            ``tenant_id`` is not an accident here: platform spend is ledgered under no
            tenant by design, and a corpus without that slice would leave the
            platform-admin surfaces reading as though the platform itself never ran.
        actors: ``(user_id, username)`` for every principal runs are attributed to.
        share: This scope's share of the daily volume.
    """

    label: str
    tenant_id: int | None
    actors: tuple[tuple[int, str], ...]
    share: float


class DemoScopesMissingError(RuntimeError):
    """The base seed has not run, so there is nothing to attribute a corpus to."""


async def resolve_scopes() -> tuple[DemoScope, ...]:
    """Return the scopes the corpus is written under, read from the seeded rows.

    Read from the database rather than restated here: the tenant ids and user ids are
    whatever ``app.seed`` created, and a corpus attributed to invented ids would be
    invisible to every screen (and, under RLS, refused).

    Returns:
        The two seeded tenants and the platform slice, in volume order.

    Raises:
        DemoScopesMissingError: If ``app.seed`` has not been run.
    """
    async with get_sessionmaker()() as session:
        # ``None`` is the deliberate platform-wide assertion, not an unbound read: this
        # is a platform tool enumerating every tenant it will write for.
        await set_tenant_scope(session, None)
        tenants = {
            name: tid
            for name, tid in (
                await session.execute(select(Tenant.name, Tenant.id))
            ).all()
        }
        users = (
            await session.execute(
                select(User.id, User.username, User.tenant_id).order_by(User.id)
            )
        ).all()

    by_tenant: dict[int | None, list[tuple[int, str]]] = {}
    for user_id, username, tenant_id in users:
        by_tenant.setdefault(tenant_id, []).append((user_id, username))

    scopes: list[DemoScope] = []
    shares = (0.45, 0.32)
    for spec, share in zip(TENANTS, shares, strict=True):
        tenant_id = tenants.get(spec.name)
        if tenant_id is None or not by_tenant.get(tenant_id):
            raise DemoScopesMissingError(
                f"tenant {spec.name!r} (or its users) is missing. Run "
                "`python -m app.seed` before `python -m app.demo`."
            )
        scopes.append(
            DemoScope(
                label=spec.name,
                tenant_id=tenant_id,
                actors=tuple(by_tenant[tenant_id]),
                share=share,
            )
        )

    platform = by_tenant.get(None) or []
    if not platform:
        raise DemoScopesMissingError(
            "no platform principals exist. Run `python -m app.seed` before "
            "`python -m app.demo`."
        )
    scopes.append(
        DemoScope(label="Platform", tenant_id=None, actors=tuple(platform), share=0.23)
    )
    return tuple(scopes)


# ─────────────────────────────────────────────────────────────────────────────
# The corpus — pure, deterministic, and computed with no database in sight
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class DemoCorpus:
    """Every row the corpus consists of, as column dictionaries, keyed by scope.

    Held as plain dicts rather than ORM instances so the whole thing can be inserted
    with one ``executemany`` per table per scope, and so :func:`build_corpus` can be
    exercised — including the shape of the ledger series — without a database.

    Attributes:
        ledger: ``usage_ledger`` rows.
        runs: ``runs`` rows, each one a fold over this corpus' own ``events``.
        events: ``run_events`` rows.
        jobs: ``job_runs`` rows.
        audit: ``audit_log`` rows.
        approvals: ``approvals`` rows.
    """

    ledger: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    jobs: list[dict[str, Any]] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)

    def extend(self, other: DemoCorpus) -> None:
        """Append every row of ``other`` onto this corpus."""
        self.ledger.extend(other.ledger)
        self.runs.extend(other.runs)
        self.events.extend(other.events)
        self.jobs.extend(other.jobs)
        self.audit.extend(other.audit)
        self.approvals.extend(other.approvals)

    def counts(self) -> dict[str, int]:
        """Return the row count per table name."""
        return {
            "usage_ledger": len(self.ledger),
            "runs": len(self.runs),
            "run_events": len(self.events),
            "job_runs": len(self.jobs),
            "audit_log": len(self.audit),
            "approvals": len(self.approvals),
        }


@dataclass(slots=True)
class _Counter:
    """Monotonic per-kind counters, so every tagged id in one corpus is unique."""

    run: int = 0
    job: int = 0
    gate: int = 0

    def next_run(self) -> str:
        """Return the next tagged run id."""
        self.run += 1
        return _tag("run", self.run)

    def next_job(self) -> str:
        """Return the next tagged workflow id."""
        self.job += 1
        return _tag("job", self.job)

    def next_gate(self) -> str:
        """Return the next tagged approval id."""
        self.gate += 1
        return _tag("gate", self.gate)


def _weighted(rng: random.Random, mix: Sequence[tuple[str, int]]) -> str:
    """Draw one deployment id from a ``(id, weight)`` mix."""
    return rng.choices([m[0] for m in mix], weights=[m[1] for m in mix])[0]


def _day_volume(rng: random.Random, day_index: int, day: datetime) -> int:
    """Return how many agent runs happen on ``day``, across every scope.

    The three factors are the whole shape of the series: the weekday cycle the
    forecaster's ``season_length=7`` assumes, a mild growth trend for it to project,
    and the two spikes that give the conformal band residuals worth calibrating on.

    Args:
        rng: The seeded generator.
        day_index: ``0`` for the oldest day in the window.
        day: The day itself, for its weekday.

    Returns:
        A run count of at least one — every scope must appear on the first and last
        day of the window, or its gap-filled series would be shorter than the window
        and the forecast's history floor would be measured against the wrong length.
    """
    trend = 0.82 + 0.36 * (day_index / max(1, DEMO_HISTORY_DAYS - 1))
    factor = _WEEKDAY_FACTOR[day.weekday()] * trend * _SPIKES.get(day_index, 1.0)
    noise = rng.gauss(1.0, 0.10)
    return max(len(TENANTS) + 1, round(_RUNS_PER_DAY * factor * max(0.4, noise)))


def _ledger_row(
    *,
    scope: DemoScope,
    user_id: int,
    ts: datetime,
    deployment: str,
    role: ModelRole,
    prompt_tokens: int,
    completion_tokens: int,
    trace_id: str,
    audio_seconds: float = 0.0,
    images: int = 0,
) -> dict[str, Any]:
    """Build one ``usage_ledger`` row, priced by the fleet's own declared rates.

    The cost is never invented: :func:`aegis.gateway.routing.unit_cost` is the same
    function the gateway prices a real call with, and it is given the *deployment* so
    a tenant-selected model costs what it costs rather than what its tier costs.
    """
    return {
        "tenant_id": scope.tenant_id,
        "user_id": user_id,
        "ts": ts,
        "model": deployment,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "audio_seconds": audio_seconds,
        "images": images,
        "cost_usd": unit_cost(
            role,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            audio_seconds=audio_seconds,
            images=images,
            deployment=deployment,
        ),
        "trace_id": trace_id,
    }


def _run_calls(
    rng: random.Random,
    *,
    scope: DemoScope,
    user_id: int,
    started: datetime,
    trace_id: str,
    fleet: dict[str, ModelRole],
) -> list[dict[str, Any]]:
    """Build the ledger rows one agent run's model calls produce.

    A turn on this platform is not one call: two guardrail classifier calls (input and
    output rails), one embedding for the retrieval query, and two to five heavy calls
    for routing, planning and generation. Writing only the generation call would
    understate the volume by five to one and would leave the cheap tier — the whole
    reason the routing table exists — invisible on the spend-by-model chart.
    """
    rows: list[dict[str, Any]] = []
    offset = 0.0

    def at(seconds: float) -> datetime:
        return started + timedelta(seconds=seconds)

    for _ in range(2):
        guard = _weighted(rng, _GUARD_MIX)
        offset += rng.uniform(0.2, 1.4)
        rows.append(
            _ledger_row(
                scope=scope,
                user_id=user_id,
                ts=at(offset),
                deployment=guard,
                role=fleet[guard],
                prompt_tokens=rng.randint(700, 2_600),
                completion_tokens=rng.randint(6, 40),
                trace_id=trace_id,
            )
        )

    offset += rng.uniform(0.1, 0.6)
    rows.append(
        _ledger_row(
            scope=scope,
            user_id=user_id,
            ts=at(offset),
            deployment=_EMBEDDING_DEPLOYMENT,
            role=fleet[_EMBEDDING_DEPLOYMENT],
            prompt_tokens=rng.randint(400, 3_200),
            completion_tokens=0,
            trace_id=trace_id,
        )
    )

    for _ in range(rng.randint(2, 5)):
        heavy = _weighted(rng, _HEAVY_MIX)
        offset += rng.uniform(0.8, 6.0)
        rows.append(
            _ledger_row(
                scope=scope,
                user_id=user_id,
                ts=at(offset),
                deployment=heavy,
                role=fleet[heavy],
                prompt_tokens=rng.randint(6_000, 30_000),
                completion_tokens=rng.randint(300, 1_800),
                trace_id=trace_id,
            )
        )

    if rng.random() < 0.06:
        vision = _weighted(rng, _VISION_MIX)
        offset += rng.uniform(0.5, 3.0)
        rows.append(
            _ledger_row(
                scope=scope,
                user_id=user_id,
                ts=at(offset),
                deployment=vision,
                role=fleet[vision],
                prompt_tokens=rng.randint(1_200, 4_000),
                completion_tokens=rng.randint(80, 500),
                images=rng.randint(1, 3),
                trace_id=trace_id,
            )
        )
    return rows


def _run_events(
    rng: random.Random,
    *,
    run_id: str,
    trace_id: str,
    started: datetime,
    calls: list[dict[str, Any]],
    status: RunStatus,
    gate: tuple[str, Any] | None,
    agent_id: str,
) -> list[RunEventRecord]:
    """Build one run's event stream — the log the ``runs`` row is folded from.

    Args:
        rng: The seeded generator.
        run_id: The run's tagged id.
        trace_id: The trace every row of this run shares.
        started: When the run started.
        calls: The run's ledger rows, so the terminal event reports the usage the
            ledger actually holds rather than a second, unreconciled figure.
        status: How the run ended.
        gate: ``(approval_id, args)`` when this run parked at the human gate.
        agent_id: The roster specialist the run was handled by.

    Returns:
        The stream, ordered by ``seq``.
    """
    records: list[RunEventRecord] = []
    seq = 0
    clock = started

    def emit(payload: dict[str, Any], *, advance_ms: int = 0) -> None:
        nonlocal seq, clock
        payload = {**payload, "seq": seq}
        records.append(
            RunEventRecord(
                event_type=str(payload["type"]),
                seq=seq,
                ts=clock,
                payload=payload,
                agent_id=agent_id,
                trace_id=trace_id,
            )
        )
        seq += 1
        clock += timedelta(milliseconds=advance_ms)

    emit({"type": "run_started", "trace_id": trace_id})

    blocked = status is RunStatus.BLOCKED
    nodes = _RUN_NODES[:1] if blocked else _RUN_NODES
    for node, label in nodes:
        emit({"type": "node_started", "node": node, "label": label})
        duration = rng.randint(180, 4_200)
        emit(
            {
                "type": "node_finished",
                "node": node,
                "label": label,
                "duration_ms": duration,
            },
            advance_ms=duration,
        )
        if node == "guard_input" and blocked:
            emit(
                {
                    "type": "guardrail",
                    "stage": "input",
                    "verdict": "block",
                    "reason": "Prompt-injection signature matched on the inbound turn.",
                    "layer": "deterministic",
                    "redactions": [],
                }
            )

    if not blocked and gate is not None:
        gate_id, gate_args = gate
        tool = _gated_tool()
        emit(
            {
                "type": "tool_call",
                "call_id": f"{gate_id}-1",
                "tool": tool.name,
                "args": gate_args,
                "risk": tool.risk.value,
            }
        )
        emit(
            {
                "type": "approval_required",
                "approval_id": gate_id,
                "action": tool.name,
                "args": gate_args,
                "risk": tool.risk.value,
                "rationale": _gate_rationale(tool),
                "actions": [
                    {
                        "id": f"{gate_id}-1",
                        "name": tool.name,
                        "args": gate_args,
                        "risk": tool.risk.value,
                    }
                ],
            }
        )
    elif not blocked and rng.random() < 0.35:
        tool = _low_risk_tool()
        call_id = f"{run_id}-1"
        emit(
            {
                "type": "tool_call",
                "call_id": call_id,
                "tool": tool.name,
                "args": _example_args(tool, rng, int(run_id.rsplit("-", 1)[-1])),
                "risk": tool.risk.value,
            }
        )
        emit(
            {
                "type": "tool_result",
                "call_id": call_id,
                "ok": True,
                "summary": f"{tool.name} completed without escalation.",
            }
        )

    if status is RunStatus.ERROR:
        emit(
            {
                "type": "error",
                "message": "Upstream deployment returned 429 after three retries.",
            }
        )

    prompt_tokens = sum(int(c["prompt_tokens"]) for c in calls)
    completion_tokens = sum(int(c["completion_tokens"]) for c in calls)
    emit(
        {
            "type": "run_finished",
            "status": status.value,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": round(sum(float(c["cost_usd"]) for c in calls), 6),
            "cache_hit": rng.random() < 0.18,
        }
    )
    return records


def _gated_tool() -> Any:  # noqa: ANN401 - the adapter's ToolSpec, not re-exported
    """Return the tool a demo gate authorises: the registry's highest-risk one.

    Selected **by risk tier**, never by name. Which call needs a person is a property
    of the risk the domain declared on it, and the platform's own rule is "the
    highest-risk proposal is what the gate is about" — so this keeps working, and keeps
    meaning the same thing, when the registry holds somebody else's three tools.
    """
    order = {level: index for index, level in enumerate(RiskLevel)}
    return max(TOOL_REGISTRY.values(), key=lambda spec: order[spec.risk])


def _low_risk_tool() -> Any:  # noqa: ANN401 - the adapter's ToolSpec, not re-exported
    """Return the registry's lowest-risk tool — the one a run may just execute."""
    order = {level: index for index, level in enumerate(RiskLevel)}
    return min(TOOL_REGISTRY.values(), key=lambda spec: order[spec.risk])


def _example_args(spec: Any, rng: random.Random, index: int) -> dict[str, Any]:  # noqa: ANN401
    """Build schema-valid placeholder arguments from a tool's own argument model.

    Read off ``args_model.model_fields`` rather than written out, because a literal
    argument dict is a core module spelling one domain's field names — the exact defect
    the conformance scanner exists to catch. Every string value carries
    :data:`DEMO_PREFIX`, so a gate card cannot be mistaken for one raised over a real
    record even before anybody checks the id.

    Args:
        spec: The tool whose arguments to build.
        rng: The seeded generator, used to vary enum choices.
        index: A per-gate counter, so two gates do not name the same record.

    Returns:
        One value per field whose type this can honestly fill; anything else is left
        out rather than guessed at, since an optional field absent is valid and an
        optional field wrong is not.
    """
    args: dict[str, Any] = {}
    for name, field_info in spec.args_model.model_fields.items():
        value = _placeholder(field_info.annotation, name, rng, index)
        if value is not None:
            args[name] = value
    return args


def _placeholder(  # noqa: PLR0911 - one branch per JSON-representable kind
    annotation: Any,  # noqa: ANN401 - an arbitrary type annotation
    name: str,
    rng: random.Random,
    index: int,
) -> Any:  # noqa: ANN401
    """Return a schema-valid placeholder for one annotated field, or ``None``."""
    for candidate in get_args(annotation) or (annotation,):
        if candidate is type(None):
            continue
        if isinstance(candidate, type) and issubclass(candidate, Enum):
            return rng.choice(list(candidate)).value
        if candidate is bool:
            return False
        if candidate is int:
            return index
        if candidate is float:
            return float(index)
        if candidate is str:
            return f"{DEMO_PREFIX}{name}-{index:06d}"
    return None


def _gate_rationale(tool: Any) -> str:  # noqa: ANN401 - the adapter's ToolSpec
    """Return the sentence a reviewer reads on this run's approval card.

    Built from what the platform actually knows about the call — the risk the registry
    declared, and whether the tool says it overwrites state — rather than from a
    sentence about this domain that a retarget would leave behind unchanged and wrong.
    """
    consequence = (
        "overwrites state a reader would miss"
        if tool.destructive
        else "is not reversible from the agent's side"
    )
    return (
        f"{tool.name} is declared {tool.risk.value} risk by the tool registry and "
        f"{consequence}, so the write waits for a person."
    )


def _event_rows(
    records: Sequence[RunEventRecord],
    *,
    run_id: str,
    tenant_id: int | None,
    job_id: int | None = None,
) -> list[dict[str, Any]]:
    """Project event records onto ``run_events`` column dictionaries."""
    return [
        {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "seq": record.seq,
            "ts": record.ts,
            "event_type": record.event_type,
            "agent_id": record.agent_id,
            "job_id": job_id,
            "trace_id": record.trace_id,
            "span_id": record.span_id,
            "payload": dict(record.payload),
        }
        for record in records
    ]


def _audit_row(
    *,
    tenant_id: int | None,
    ts: datetime,
    action: str,
    actor: str,
    trace_id: str,
    payload: dict[str, Any],
    model: str | None = None,
    approved_by: str | None = None,
) -> dict[str, Any]:
    """Build one ``audit_log`` row."""
    return {
        "tenant_id": tenant_id,
        "ts": ts,
        "action": action,
        "actor": actor,
        "model": model,
        "trace_id": trace_id,
        "payload": payload,
        "approved_by": approved_by,
    }


def build_corpus(
    scopes: Sequence[DemoScope],
    *,
    now: datetime | None = None,
    seed: int = DEMO_RNG_SEED,
    days: int = DEMO_HISTORY_DAYS,
) -> DemoCorpus:
    """Fabricate the whole corpus, deterministically, with no database involved.

    Args:
        scopes: The tenants (and the platform slice) to attribute rows to.
        now: The instant the window ends at; defaults to the wall clock.
        seed: RNG seed. Fixed by default, so the corpus is reproducible.
        days: How many days of history to span.

    Returns:
        Every row, as column dictionaries, ready for a bulk insert.
    """
    rng = random.Random(seed)
    fleet = allowed_deployments()
    counter = _Counter()
    end = (now or datetime.now(UTC)).astimezone(UTC)
    first = (end - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # Which specialists a run may be attributed to, read from the roster rather than
    # named: piece 8 is rewritten on swap day and a literal here would caption every
    # demo run with a specialist the new roster has never heard of. The roster's own
    # default carries most of the traffic, which is what "default" means — an even
    # split would put as many runs through a narrow specialist as through the general
    # one, and the runs list would misreport where the work actually goes.
    specialists = agent_roster().specialists
    agents = tuple(s.role for s in specialists) or ("default",)
    agent_weights = tuple(6 if s.is_default else 1 for s in specialists) or (1,)

    corpus = DemoCorpus()
    for day_index in range(days):
        day = first + timedelta(days=day_index)
        total = _day_volume(rng, day_index, day)
        for scope in scopes:
            share = max(1, round(total * scope.share))
            for _ in range(share):
                corpus.extend(
                    _build_run(
                        rng,
                        scope=scope,
                        day=day,
                        counter=counter,
                        fleet=fleet,
                        end=end,
                        agents=agents,
                        agent_weights=agent_weights,
                    )
                )
            corpus.extend(
                _build_background(rng, scope=scope, day=day, fleet=fleet, end=end)
            )
            corpus.extend(
                _build_jobs(rng, scope=scope, day=day, counter=counter, end=end)
            )
    return corpus


def _build_run(
    rng: random.Random,
    *,
    scope: DemoScope,
    day: datetime,
    counter: _Counter,
    fleet: dict[str, ModelRole],
    end: datetime,
    agents: tuple[str, ...],
    agent_weights: tuple[int, ...],
) -> DemoCorpus:
    """Build one agent run: its ledger rows, its events, its header and its trail."""
    corpus = DemoCorpus()
    user_id, username = rng.choice(scope.actors)
    # Working hours, so the hourly distribution is not a flat smear across midnight.
    started = day + timedelta(
        hours=rng.randint(7, 19), minutes=rng.randint(0, 59), seconds=rng.randint(0, 59)
    )
    # Clamped into ``[day, end]``, not just below ``end``: on the newest day a run drawn
    # for 19:00 has not happened yet, and subtracting an hour from a run seeded at 00:30
    # would push it into *yesterday's* bucket — leaving the newest day empty and the
    # gap-filled series one observation short of the window it claims to cover.
    started = max(day, min(started, end))

    run_id = counter.next_run()
    trace_id = _trace(rng)

    roll = rng.random()
    if roll < 0.035:
        status = RunStatus.BLOCKED
    elif roll < 0.06:
        status = RunStatus.ERROR
    else:
        status = RunStatus.COMPLETED

    gate: tuple[str, dict[str, Any]] | None = None
    if status is RunStatus.COMPLETED and rng.random() < 0.05:
        gate_id = counter.next_gate()
        gate = (gate_id, _example_args(_gated_tool(), rng, counter.gate))
    agent_id = rng.choices(agents, weights=agent_weights)[0]

    calls = (
        []
        if status is RunStatus.BLOCKED
        else _run_calls(
            rng,
            scope=scope,
            user_id=user_id,
            started=started,
            trace_id=trace_id,
            fleet=fleet,
        )
    )
    # A blocked run still pays for the rail that blocked it.
    if status is RunStatus.BLOCKED:
        guard = _weighted(rng, _GUARD_MIX)
        calls = [
            _ledger_row(
                scope=scope,
                user_id=user_id,
                ts=started + timedelta(seconds=rng.uniform(0.2, 1.2)),
                deployment=guard,
                role=fleet[guard],
                prompt_tokens=rng.randint(500, 2_000),
                completion_tokens=rng.randint(4, 24),
                trace_id=trace_id,
            )
        ]
    corpus.ledger.extend(calls)

    records = _run_events(
        rng,
        run_id=run_id,
        trace_id=trace_id,
        started=started,
        calls=calls,
        status=status,
        gate=gate,
        agent_id=agent_id,
    )
    header = fold_events(
        run_id, records, tenant_id=scope.tenant_id, user_id=user_id
    )
    corpus.events.extend(
        _event_rows(records, run_id=run_id, tenant_id=scope.tenant_id)
    )
    corpus.runs.append(header.as_columns())

    heavy = [c for c in calls if fleet[str(c["model"])] is ModelRole.GENERATION]
    corpus.audit.append(
        _audit_row(
            tenant_id=scope.tenant_id,
            ts=started,
            action="query.start",
            actor=username,
            trace_id=trace_id,
            model=str(heavy[0]["model"]) if heavy else None,
            payload={
                "run_id": run_id,
                "agent_id": agent_id,
                "nodes": header.node_count,
                "model_calls": len(calls),
                "cache_hit": header.cache_hit,
            },
        )
    )
    if status is RunStatus.BLOCKED:
        corpus.audit.append(
            _audit_row(
                tenant_id=scope.tenant_id,
                ts=started + timedelta(seconds=1),
                action="guardrail.input",
                actor=username,
                trace_id=trace_id,
                payload={
                    "run_id": run_id,
                    "verdict": "block",
                    "layer": "deterministic",
                },
            )
        )

    if gate is not None:
        corpus.approvals.append(
            _build_approval(
                rng,
                gate=gate,
                run_id=run_id,
                trace_id=trace_id,
                scope=scope,
                user_id=user_id,
                raised_at=header.finished_at or started,
                end=end,
            )
        )
        decision = corpus.approvals[-1]
        if decision["decided_at"] is not None:
            corpus.audit.append(
                _audit_row(
                    tenant_id=scope.tenant_id,
                    ts=decision["decided_at"],
                    action="approval.decision",
                    actor=str(decision["decided_by"]),
                    trace_id=trace_id,
                    approved_by=str(decision["decided_by"]),
                    payload={
                        "approval_id": gate[0],
                        "run_id": run_id,
                        "decision": str(decision["status"].value),
                    },
                )
            )
    return corpus


def _build_approval(
    rng: random.Random,
    *,
    gate: tuple[str, dict[str, Any]],
    run_id: str,
    trace_id: str,
    scope: DemoScope,
    user_id: int,
    raised_at: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Build one ``approvals`` row for a run that parked at the human gate.

    Most gates in the window are decided; the ones raised in the last three days are
    left ``PENDING`` so the inbox has something live to act on. Their SLA deadline is
    :data:`_DEMO_APPROVAL_SLA` out, for the reason ``app.seed`` widens its own: the
    sweeper is real, and a gate seeded inside the configured window is auto-rejected
    before anyone opens the screen.
    """
    gate_id, args = gate
    tool = _gated_tool()
    recent = (end - raised_at) < timedelta(days=3)
    approver = next(
        (name for uid, name in scope.actors if uid != user_id), scope.actors[0][1]
    )

    if recent:
        status = ApprovalStatus.PENDING
        decided_at: datetime | None = None
        decided_by: str | None = None
    else:
        status = rng.choices(
            [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED],
            weights=[6, 3, 1],
        )[0]
        decided_at = raised_at + timedelta(minutes=rng.randint(3, 900))
        decided_by = approver if status is not ApprovalStatus.EXPIRED else "sla-sweeper"

    return {
        "id": gate_id,
        "run_id": run_id,
        "thread_id": run_id,
        "tenant_id": scope.tenant_id,
        "status": status,
        "persona": persona_for_role(_role_of(scope, user_id)),
        "action": tool.name,
        "args": args,
        "risk": tool.risk,
        "rationale": _gate_rationale(tool),
        "ml_snapshot": {},
        "actions": [
            {
                "id": f"{gate_id}-1",
                "name": tool.name,
                "args": args,
                "risk": tool.risk.value,
            }
        ],
        "requested_by": user_id,
        "trace_id": trace_id,
        "assignee_tier": "tenant_admin" if scope.tenant_id else "platform_admin",
        "sla_deadline": raised_at + _DEMO_APPROVAL_SLA,
        "created_at": raised_at,
        "decided_at": decided_at,
        "decided_by": decided_by,
    }


def _role_of(scope: DemoScope, user_id: int) -> Role:
    """Return the governance role of ``user_id`` for the persona lookup.

    The corpus does not carry roles, and it does not need to: the persona is chosen
    through the adapter seam (:func:`app.adapter.persona_for_role`) exactly as
    ``app.seed`` chooses it, so a retarget re-personas these rows with everything else.
    """
    return Role.CLIENT if user_id == scope.actors[-1][0] else Role.ADMIN


def _build_background(
    rng: random.Random,
    *,
    scope: DemoScope,
    day: datetime,
    fleet: dict[str, ModelRole],
    end: datetime,
) -> DemoCorpus:
    """Build the day's model calls that belong to no agent run.

    Ingestion embeddings and voice transcriptions are real spend with no run behind
    them, and they are the two rows that prove the ledger's non-token billing units
    are carried end to end: whisper is billed per minute of audio, so a transcription
    ledgered with ``prompt_tokens=0`` would cost ``$0.00`` and a USD cap would not
    bind on it.
    """
    corpus = DemoCorpus()
    for _ in range(rng.randint(2, 9)):
        user_id, _username = rng.choice(scope.actors)
        ts = day + timedelta(
            hours=rng.randint(6, 21), minutes=rng.randint(0, 59)
        )
        if ts > end:
            continue
        trace_id = _trace(rng)
        if rng.random() < 0.78:
            corpus.ledger.append(
                _ledger_row(
                    scope=scope,
                    user_id=user_id,
                    ts=ts,
                    deployment=_EMBEDDING_DEPLOYMENT,
                    role=fleet[_EMBEDDING_DEPLOYMENT],
                    prompt_tokens=rng.randint(4_000, 40_000),
                    completion_tokens=0,
                    trace_id=trace_id,
                )
            )
        else:
            corpus.ledger.append(
                _ledger_row(
                    scope=scope,
                    user_id=user_id,
                    ts=ts,
                    deployment=_VOICE_DEPLOYMENT,
                    role=fleet[_VOICE_DEPLOYMENT],
                    prompt_tokens=0,
                    completion_tokens=0,
                    audio_seconds=float(rng.randint(25, 900)),
                    trace_id=trace_id,
                )
            )
    return corpus


def _build_jobs(
    rng: random.Random,
    *,
    scope: DemoScope,
    day: datetime,
    counter: _Counter,
    end: datetime,
) -> DemoCorpus:
    """Build the day's durable background jobs for one scope."""
    corpus = DemoCorpus()
    for _ in range(rng.choices([0, 1, 2, 3], weights=[3, 5, 3, 1])[0]):
        user_id, username = rng.choice(scope.actors)
        created = day + timedelta(hours=rng.randint(5, 22), minutes=rng.randint(0, 59))
        if created > end:
            continue
        job_type = _weighted(rng, _JOB_TYPES)
        status = rng.choices(
            [
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
                JobStatus.RUNNING,
            ],
            weights=[16, 2, 1, 1],
        )[0]
        started = created + timedelta(seconds=rng.randint(1, 40))
        runtime = timedelta(seconds=rng.randint(20, 900))
        finished = (
            None if status is JobStatus.RUNNING else min(started + runtime, end)
        )
        workflow_id = counter.next_job()
        corpus.jobs.append(
            {
                "tenant_id": scope.tenant_id,
                "user_id": user_id,
                "job_type": job_type,
                "workflow_id": workflow_id,
                "run_id": f"{workflow_id}-attempt-1",
                "status": status,
                "completed_stage": (
                    "embed" if status is JobStatus.SUCCEEDED else "parse"
                ),
                "payload": {"pages": rng.randint(2, 180)},
                "result": (
                    {"chunks": rng.randint(20, 900)}
                    if status is JobStatus.SUCCEEDED
                    else {}
                ),
                "error": (
                    "Parse stage exceeded its page budget."
                    if status is JobStatus.FAILED
                    else None
                ),
                "cost_usd": round(rng.uniform(0.01, 0.85), 6),
                "created_at": created,
                "started_at": started,
                "finished_at": finished,
                "cancelled_by": username if status is JobStatus.CANCELLED else None,
            }
        )
        corpus.audit.append(
            _audit_row(
                tenant_id=scope.tenant_id,
                ts=created,
                action="documents.upload" if job_type == "ingest" else "jobs.requeue",
                actor=username,
                trace_id=_trace(rng),
                payload={"workflow_id": workflow_id, "job_type": job_type},
            )
        )
    return corpus


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class DemoSummary:
    """What one demo run wrote, per table.

    Counted rather than assumed: a second run of an idempotent seeder must report every
    table as already present, and a summary that cannot tell the two apart cannot prove
    it.
    """

    created: dict[str, int] = field(default_factory=dict)
    existing: dict[str, int] = field(default_factory=dict)

    def record(self, table: str, *, created: int = 0, existing: int = 0) -> None:
        """Count rows against ``table``."""
        if created:
            self.created[table] = self.created.get(table, 0) + created
        if existing:
            self.existing[table] = self.existing.get(table, 0) + existing

    @property
    def total_created(self) -> int:
        """Return the number of rows this run inserted."""
        return sum(self.created.values())

    def lines(self) -> list[str]:
        """Return one human-readable line per table, in table order."""
        tables = sorted(set(self.created) | set(self.existing))
        return [
            f"  {table:<14} {self.created.get(table, 0):>6} written, "
            f"{self.existing.get(table, 0):>6} already present"
            for table in tables
        ]


@dataclass(slots=True)
class WipeSummary:
    """How many demo rows ``--wipe`` removed, per table."""

    deleted: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """Return the total number of rows deleted."""
        return sum(self.deleted.values())

    def lines(self) -> list[str]:
        """Return one human-readable line per table, in table order."""
        return [
            f"  {table:<14} {count:>6} deleted"
            for table, count in sorted(self.deleted.items())
        ]


#: Rows per ``executemany``. Large enough that ninety days is a handful of round trips,
#: small enough that no single statement carries a hundred thousand bind parameters.
_CHUNK = 2_000


async def _existing(model: Any, column: Any) -> int:  # noqa: ANN401 - mapped class/column
    """Return how many demo-tagged rows ``model`` already holds."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, None)
        return (
            await session.execute(
                select(func.count())
                .select_from(model)
                .where(column.like(f"{DEMO_PREFIX}%"))
            )
        ).scalar_one()


async def _insert_scoped(
    model: Any,  # noqa: ANN401 - mapped class
    rows: Sequence[dict[str, Any]],
    *,
    tenant_id: int | None,
) -> int:
    """Insert ``rows`` under ``tenant_id``'s bound RLS scope, in chunks.

    The scope is bound **inside each transaction**, because the GUC the policies read
    is transaction-local: binding once and committing between chunks would leave every
    chunk after the first writing with no scope at all.
    """
    written = 0
    for start in range(0, len(rows), _CHUNK):
        chunk = rows[start : start + _CHUNK]
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            await session.execute(insert(model), list(chunk))
            await session.commit()
        written += len(chunk)
    return written


async def _write_corpus(corpus: DemoCorpus, summary: DemoSummary) -> None:
    """Write every table of ``corpus``, each scope under its own bound scope.

    Rows are grouped by their own ``tenant_id`` and inserted under exactly that scope,
    so a row can only ever be written into the tenant it names — the ``WITH CHECK`` half
    of the ``tenant_isolation`` policy refuses anything else, which is what makes "a
    tenant never ends up with another's rows" a property of the database rather than of
    this module's care.
    """
    tables: tuple[tuple[str, Any, Any, list[dict[str, Any]]], ...] = (
        ("job_runs", JobRun, JobRun.workflow_id, corpus.jobs),
        ("usage_ledger", UsageLedger, UsageLedger.trace_id, corpus.ledger),
        ("audit_log", AuditLog, AuditLog.trace_id, corpus.audit),
        ("run_events", RunEvent, RunEvent.run_id, corpus.events),
        ("runs", Run, Run.run_id, corpus.runs),
        ("approvals", Approval, Approval.id, corpus.approvals),
    )
    for name, model, column, rows in tables:
        already = await _existing(model, column)
        if already:
            summary.record(name, existing=already)
            continue
        by_scope: dict[int | None, list[dict[str, Any]]] = {}
        for row in rows:
            by_scope.setdefault(row.get("tenant_id"), []).append(row)
        written = 0
        for tenant_id, scoped in by_scope.items():
            written += await _insert_scoped(model, scoped, tenant_id=tenant_id)
        summary.record(name, created=written)


async def _write_redteam(scopes: Sequence[DemoScope], summary: DemoSummary) -> None:
    """Run the offline battery for real, and store the reports as history.

    Offline means no completer, which means the deterministic backstops only: no API
    key, no network and no spend. The verdicts stored are therefore the rails' actual
    verdicts — a fabricated red-team report would be the one row in this corpus that
    could mislead somebody about whether the product is safe.
    """
    from aegis.redteam.battery import battery_for, suite_for  # noqa: PLC0415
    from aegis.redteam.runner import RedTeamThresholds, run_redteam  # noqa: PLC0415
    from aegis.redteam.store import record_run  # noqa: PLC0415

    already = await _existing(RedTeamRun, RedTeamRun.run_id)
    if already:
        summary.record("redteam_runs", existing=already)
        return

    now = datetime.now(UTC)
    written = 0
    targets = [scopes[-1], scopes[0]]  # the platform's own rails, then a tenant's
    for index, suite_id in enumerate(_REDTEAM_SUITES):
        suite = suite_for(suite_id)
        probes = battery_for(suite)
        thresholds = RedTeamThresholds(min_block_rate=suite.offline_floor)
        for offset, scope in enumerate(targets):
            report = await run_redteam(battery=probes, thresholds=thresholds)
            payload = report.as_dict()
            payload["suite"] = suite.id
            payload["mode"] = "offline"
            run_id = _tag("rt", index * len(targets) + offset + 1)
            async with get_sessionmaker()() as session:
                await set_tenant_scope(session, scope.tenant_id)
                row = await record_run(
                    session,
                    run_id=run_id,
                    tenant_id=scope.tenant_id,
                    suite=suite.id,
                    mode="offline",
                    duration_ms=1_200 + 400 * offset,
                    initiated_by=scope.actors[0][1],
                    initiated_role="devops",
                    report=payload,
                    estimated_cost_usd=0.0,
                )
                row.started_at = now - timedelta(days=7 * (index + 1) + offset)
                await session.commit()
            written += 1
    summary.record("redteam_runs", created=written)


async def seed_demo(
    *, now: datetime | None = None, days: int = DEMO_HISTORY_DAYS
) -> DemoSummary:
    """Write the demo corpus, and return what it did.

    Args:
        now: The instant the history window ends at; defaults to the wall clock.
        days: How many days of history to write.

    Returns:
        The per-table written/already-present counts. A second run returns a summary
        whose :attr:`DemoSummary.total_created` is ``0``.

    Raises:
        DemoScopesMissingError: If ``app.seed`` has not been run.
    """
    scopes = await resolve_scopes()
    end = (now or datetime.now(UTC)).astimezone(UTC)

    # ``run_events`` is PARTITIONED BY RANGE (ts) and the boot only creates the current
    # month and the next. Ninety days of history reaches back three or four months, and
    # PostgreSQL rejects a row with nowhere to go (``RunPartitionMissingError``), so the
    # months this corpus spans are created first — on the owner engine, because
    # attaching a partition is DDL the serving role deliberately cannot do.
    await ensure_run_event_partitions(
        get_admin_engine(), moment=end - timedelta(days=days), months_ahead=days // 28 + 1
    )

    summary = DemoSummary()
    corpus = build_corpus(scopes, now=end, days=days)
    await _write_corpus(corpus, summary)
    await _write_redteam(scopes, summary)
    return summary


async def wipe_demo() -> WipeSummary:
    """Delete every demo-tagged row, and report what each table gave up.

    One indexed ``LIKE 'demo-%'`` per table, in :data:`TAGGED_COLUMN` order, which is
    ordered so ``run_events`` goes before the ``job_runs`` its ``job_id`` may reference.
    The scope bound is the platform assertion (``None``) — removing the corpus is a
    platform-wide operation by definition, and a per-tenant scope would leave the
    NULL-tenant slice behind.

    Returns:
        The per-table deleted counts.
    """
    summary = WipeSummary()
    targets = (*TAGGED_COLUMN, (RedTeamRun, RedTeamRun.run_id))
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, None)
        for model, column in targets:
            result = await session.execute(
                delete(model).where(column.like(f"{DEMO_PREFIX}%"))
            )
            summary.deleted[str(model.__tablename__)] = result.rowcount or 0
        await session.commit()
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    """Return the argument parser for ``python -m app.demo``."""
    parser = argparse.ArgumentParser(
        prog="python -m app.demo",
        description=(
            "Write (or remove) the Aegis demo corpus: ninety days of usage, runs, "
            "jobs, audit and approvals history, every row tagged 'demo-'."
        ),
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help=(
            "Delete every demo-tagged row and report what was removed. Deliberately "
            f"does NOT require {DEMO_ENV}: the removal path must not fail because a "
            "variable was not exported."
        ),
    )
    return parser


async def _run(argv: Sequence[str] | None = None) -> int:
    """Bootstrap the schema, seed or wipe, and print what happened.

    Returns:
        ``0`` on success, ``1`` when the database refused the run, ``2`` when the
        environment gate is not set.
    """
    args = _parser().parse_args(argv)
    from app.data.session import bootstrap  # noqa: PLC0415 - CLI-only dependency

    try:
        await bootstrap()
        if args.wipe:
            wiped = await wipe_demo()
            verb = "removed" if wiped.total else "nothing to remove"
            print(f"Aegis demo data {verb}")
            for line in wiped.lines():
                print(line)
            return 0

        if not demo_enabled():
            print(
                f"REFUSED  {DEMO_ENV} is not '1'.\n"
                "  The demo corpus is fabricated data. It is written only when a "
                "human asks for it by name:\n"
                f"    {DEMO_ENV}=1 python -m app.demo\n"
                "  Removing it never needs the flag:  python -m app.demo --wipe",
                file=sys.stderr,
            )
            return 2

        summary = await seed_demo()
    except DemoScopesMissingError as exc:
        print(f"DEMO FAILED  {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"DEMO FAILED  {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"DEMO FAILED  database unreachable: {exc}", file=sys.stderr)
        return 1

    verb = (
        f"seeded {summary.total_created} rows"
        if summary.total_created
        else "already seeded — nothing to do"
    )
    print(f"Aegis demo data {verb}")
    for line in summary.lines():
        print(line)
    if summary.total_created:
        print(
            f"  every row carries the {DEMO_PREFIX!r} prefix; "
            "remove it all with `python -m app.demo --wipe`"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: run the demo seeder (or the wipe) and return its exit code."""
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(_run(argv))


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
