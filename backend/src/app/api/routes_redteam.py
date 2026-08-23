"""The red-team control plane — pick a battery, run it, keep the evidence (§7.13).

``POST /redteam/run`` in :mod:`app.api.routes` calls ``await run_redteam()`` — no
completer, no battery, no thresholds — while
:func:`aegis.redteam.runner.run_redteam` has been fully parameterised the whole time,
and it persists nothing beyond three summary numbers on an audit row. So a red-team
run was a toast: the attacks, the verdicts and the rails that produced them were gone
the moment the tab closed, and "is this getting better or worse?" had no answer. That
endpoint stays where it is — the guardrails page's block-rate teaser reads it — and
this module is the surface that actually operates the harness:

``GET /redteam/suites``
    The battery catalogue: which OWASP LLM Top-10 ids each suite exercises, how many
    probes it carries at each rail stage, and **what a live run of it would cost**,
    priced off :func:`aegis.gateway.routing.unit_cost` — the same table the usage
    ledger is priced with, so the estimate before the button and the charge after it
    cannot come from two different numbers.
``POST /redteam/runs``
    Runs a suite, offline or live, against the target tenant's own rails, persists
    the whole report and returns it with the delta against the previous run.
``GET /redteam/runs`` / ``GET /redteam/runs/{run_id}``
    History, and one run in full.

**Who may pull the trigger (7.16 row 13).** A live-model run drives dozens of real
model calls against a real deployment on somebody's budget, so *starting* a run is
platform staff only — the devops role or the platform-admin tier — and a tenant admin
is refused with a sentence saying so. *Reading* a report is available to a tenant
admin, scoped to their own tenant by :func:`_scope`, because a report about their
rails is evidence they are entitled to. The refusal is here, on the server; the screen
also hides the button, and that is a courtesy, not the enforcement.

**Money.** A live run against a **tenant** goes through the same admission path as
everything else: :func:`aegis.governance.enforce_governance` is called **before** the
first probe so a tenant already at its cap is refused with a 429 rather than
discovering the breach partway through, and the run then executes inside
:func:`~aegis.governance.context.set_governance_context`, so every gateway call the
guardrail layers make is enforced and ledgered exactly like a ``/query``. Offline is
the default and spends nothing at all.

A live run with **no** tenant — Aegis attacking its own rails — is real spend that the
usage ledger does not record, and that is said here rather than left to be discovered.
:func:`app.core.llm._governed` returns ``None`` for a context whose ``tenant_id`` is
``None``, so both halves of governance (the cap and the ledger row) are gated behind a
bound tenant by design: there is no tenant to bill and no budget row to charge against.
The consequence is that :attr:`RedTeamRun.estimated_cost_usd` is the **only** cost
figure a platform-scoped run has, and it is an estimate — it must not be described
anywhere as a ledgered charge. Measured on the 2026-08-19 ``owasp-full`` live run:
zero rows in ``usage_ledger`` for its whole 175 seconds.

**What "offline" and "live" measure, said plainly.** Offline wires no completer: only
the deterministic backstops run — injection signatures, MLCommons hazard signatures,
the PII engine — so the honest headline is *"our signatures blocked N of M"*. Live
wires the platform's cheap-model completer into the same pipeline, so the semantic
probes the signatures cannot see by design become catchable and the headline becomes
*"our stack blocked N of M"*. Both numbers are real; they are not the same claim, and
``mode`` is stored on the run so a chart can never quietly mix them.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from aegis.core.models import ModelRole
from aegis.gateway.routing import model_for, unit_cost
from aegis.governance.context import (
    GovernanceContext,
    reset_governance_context,
    set_governance_context,
)
from aegis.redteam.battery import (
    DEFAULT_SUITE_ID,
    SUITES,
    Expectation,
    UnknownSuiteError,
    battery_for,
    suite_for,
)
from aegis.redteam.runner import (
    Rails,
    RedTeamThresholds,
    RunEstimate,
    check_ingest,
    check_sequence,
    estimate_run,
    run_redteam,
)
from aegis.redteam.store import RunSummary, list_runs, load_run, previous_run, record_run
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import AuthContext, _safe_audit, require_auth
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN
from app.data.session import get_sessionmaker, set_tenant_scope

__all__ = [
    "RedteamEstimateRow",
    "RedteamHistoryResponse",
    "RedteamRunDetailResponse",
    "RedteamRunRequest",
    "RedteamRunRow",
    "RedteamSuiteRow",
    "RedteamSuitesResponse",
    "mount",
    "redteam_router",
]

logger = logging.getLogger(__name__)

redteam_router = APIRouter()

#: The two run modes. ``offline`` is the default everywhere: it spends nothing, needs
#: no key, and is the one an operator can press on demo morning without thinking.
_OFFLINE = "offline"
_LIVE = "live"

#: What a principal who may read reports but not fire the weapon is told. Named
#: explicitly rather than left as a bare 403 because "you may look at this and not do
#: it" is a governance decision, and a decision nobody can read is indistinguishable
#: from a bug.
_NOT_PLATFORM_STAFF = (
    "Starting a red-team run is a platform-operator action: it drives the guardrail "
    "stack, and a live run spends real model budget. Your role may read the reports "
    "for your tenant but not start a run. Ask a platform administrator or the devops "
    "team to run the battery."
)

#: The role that must have run the guardrail's model layers. Stated here so the cost
#: estimate prices the model the run will actually call, not a generic one.
_GUARDRAIL_ROLE = ModelRole.CHEAP


# ─────────────────────────────────────────────────────────────────────────────
# Authorisation
# ─────────────────────────────────────────────────────────────────────────────


def _may_start_runs(auth: AuthContext) -> bool:
    """Whether ``auth`` may pull the trigger at all.

    Devops or the platform-admin tier, and platform staff either way — a devops
    account pinned inside a tenant is a tenant's operator, not the platform's, and
    the battery is the platform's weapon. Everything else, tenant admins included,
    is refused.
    """
    if not auth.is_platform_staff():
        return False
    return auth.fine_role == PLATFORM_ADMIN or auth.role.value == "devops"


def require_redteam_operator(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Admit only a principal allowed to *start* a red-team run (7.16 row 13)."""
    if not _may_start_runs(auth):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_PLATFORM_STAFF)
    return auth


def require_redteam_reader(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Admit a principal allowed to *read* red-team reports.

    Platform staff who may run them, plus a tenant admin — whose reads are then
    narrowed to their own tenant by :func:`_scope`. A client or a plain member is
    refused: the reports name the exact attack strings that get through, which is a
    map for anyone who wanted one.
    """
    if _may_start_runs(auth) or auth.fine_role in (PLATFORM_ADMIN, TENANT_ADMIN):
        return auth
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Reading red-team reports requires an administrator or devops role.",
    )


def _scope(auth: AuthContext) -> int | None:
    """Return the tenant filter for ``auth``'s reads, or refuse with a 403.

    ``None`` means *unrestricted* and is reachable only from a resolved platform-wide
    authority — never from a principal that merely has no tenant. Same rule, and the
    same sealed :meth:`AuthContext.tenant_scope` behind it, as every other read.
    """
    from aegis.retrieval.types import UntenantedPrincipalError, tenant_filter

    try:
        return tenant_filter(auth.tenant_scope())
    except UntenantedPrincipalError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This account is not bound to a tenant, so there is no scope to read. "
                "Ask an administrator to assign it to a tenant."
            ),
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Wire shapes
# ─────────────────────────────────────────────────────────────────────────────


class RedteamEstimateRow(BaseModel):
    """What a run of one suite will cost, before anyone presses the button."""

    probes: int = Field(description="How many prompts will be fed to a rail.")
    model_calls: int = Field(
        default=0,
        alias="modelCalls",
        description="Upper bound on completions. Zero offline — the backstops call nothing.",
    )
    prompt_tokens: int = Field(default=0, alias="promptTokens")
    completion_tokens: int = Field(default=0, alias="completionTokens")
    cost_usd: float = Field(
        default=0.0,
        alias="costUsd",
        description="Upper-bound USD, priced with the same unit_cost the ledger uses.",
    )
    model: str = Field(default="", description="The deployment the guardrail layers call.")

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())


class RedteamSuiteRow(BaseModel):
    """One selectable battery, with what it attacks and what it would cost."""

    id: str
    title: str
    summary: str
    owasp: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    attacks: int = Field(description="Adversarial probes in this suite.")
    controls: int = Field(description="Benign controls — the false-positive denominator.")
    semantic_only: int = Field(
        default=0,
        alias="semanticOnly",
        description="Probes no deterministic signature can catch; they leak offline by design.",
    )
    beyond_rails: int = Field(
        default=0,
        alias="beyondRails",
        description=(
            "Probes no rail here catches in any configuration — offline or live. Kept "
            "apart from semanticOnly because that column promises a completer would "
            "close the gap, and for these it would not."
        ),
    )
    stages: dict[str, int] = Field(
        default_factory=dict,
        description="Probe count per rail stage (input/output/tool_result/ingest/sequence).",
    )
    offline_floor: float = Field(default=0.0, alias="offlineFloor")
    live_floor: float = Field(default=0.0, alias="liveFloor")
    offline: RedteamEstimateRow
    live: RedteamEstimateRow

    model_config = ConfigDict(populate_by_name=True)


class RedteamSuitesResponse(BaseModel):
    """Body for ``GET /redteam/suites`` — the catalogue plus the caller's permissions."""

    suites: list[RedteamSuiteRow] = Field(default_factory=list)
    default_suite: str = Field(default=DEFAULT_SUITE_ID, alias="defaultSuite")
    may_run: bool = Field(
        default=False,
        alias="mayRun",
        description="Whether this principal may start a run at all (offline or live).",
    )
    may_run_live: bool = Field(
        default=False,
        alias="mayRunLive",
        description="Whether this principal may start a live-model run.",
    )
    refusal: str | None = Field(
        default=None, description="Why the caller may not start a run, when they may not."
    )

    model_config = ConfigDict(populate_by_name=True)


class RedteamRunRequest(BaseModel):
    """Body for ``POST /redteam/runs`` — every parameter the runner already accepted."""

    suite: str = Field(default=DEFAULT_SUITE_ID, description="A suite id from GET /redteam/suites.")
    mode: str = Field(default=_OFFLINE, description="'offline' (free) or 'live' (spends).")
    tenant_id: int | None = Field(
        default=None,
        alias="tenantId",
        description="Run against this tenant's rails and charge its budget. Platform staff only.",
    )
    min_block_rate: float | None = Field(
        default=None,
        alias="minBlockRate",
        ge=0.0,
        le=1.0,
        description="Override the suite's block-rate floor.",
    )
    max_false_positive_rate: float | None = Field(
        default=None,
        alias="maxFalsePositiveRate",
        ge=0.0,
        le=1.0,
        description="Override the false-positive ceiling.",
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RedteamRunRow(BaseModel):
    """One run's scalars — what a history table renders."""

    run_id: str = Field(alias="runId")
    tenant_id: int | None = Field(default=None, alias="tenantId")
    suite: str
    mode: str
    started_at: str | None = Field(default=None, alias="startedAt")
    duration_ms: int = Field(default=0, alias="durationMs")
    initiated_by: str = Field(default="", alias="initiatedBy")
    attacks_total: int = Field(default=0, alias="attacksTotal")
    attacks_blocked: int = Field(default=0, alias="attacksBlocked")
    attacks_unchecked: int = Field(
        default=0,
        alias="attacksUnchecked",
        description=(
            "Attacks refused because a rail could not run rather than because it found "
            "anything. Not part of attacksBlocked: a screen that is down stops "
            "everything and proves nothing."
        ),
    )
    block_rate: float = Field(default=0.0, alias="blockRate")
    controls_total: int = Field(default=0, alias="controlsTotal")
    false_positives: int = Field(default=0, alias="falsePositives")
    false_positive_rate: float = Field(default=0.0, alias="falsePositiveRate")
    min_block_rate: float = Field(default=0.0, alias="minBlockRate")
    max_false_positive_rate: float = Field(default=0.0, alias="maxFalsePositiveRate")
    passed: bool = False
    estimated_cost_usd: float = Field(default=0.0, alias="estimatedCostUsd")

    model_config = ConfigDict(populate_by_name=True)


class RedteamHistoryResponse(BaseModel):
    """Body for ``GET /redteam/runs`` — the trend, newest first."""

    rows: list[RedteamRunRow] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class RedteamRunDetailResponse(BaseModel):
    """Body for ``POST /redteam/runs`` and ``GET /redteam/runs/{run_id}``.

    ``report`` is the lossless :meth:`aegis.redteam.runner.RedTeamReport.as_dict`
    projection — every probe, its verdict, the rail that produced it and that rail's
    own rationale. ``previous`` is the run before this one of the *same suite in the
    same mode*, or ``null`` when there is none, which is an honest answer rather than
    a zero to draw a flattering arrow from.
    """

    run: RedteamRunRow
    report: dict[str, Any] = Field(default_factory=dict)
    previous: RedteamRunRow | None = None
    estimate: RedteamEstimateRow | None = None

    model_config = ConfigDict(populate_by_name=True)


# ─────────────────────────────────────────────────────────────────────────────
# Estimates
# ─────────────────────────────────────────────────────────────────────────────


def _price(estimate: RunEstimate) -> RedteamEstimateRow:
    """Price a :class:`RunEstimate` with the gateway's own cost table.

    Deliberately :func:`aegis.gateway.routing.unit_cost` and not a constant: it is the
    function the usage ledger falls back to, so the figure shown before the run and
    the figure charged after it are computed the same way. An estimate that came from
    somewhere else would drift, and the first person to notice would be looking at an
    invoice.
    """
    cost = (
        0.0
        if estimate.model_calls == 0
        else unit_cost(
            _GUARDRAIL_ROLE,
            prompt_tokens=estimate.prompt_tokens,
            completion_tokens=estimate.completion_tokens,
        )
    )
    return RedteamEstimateRow(
        probes=estimate.probes,
        model_calls=estimate.model_calls,
        prompt_tokens=estimate.prompt_tokens,
        completion_tokens=estimate.completion_tokens,
        cost_usd=round(cost, 6),
        model=model_for(_GUARDRAIL_ROLE) if estimate.model_calls else "",
    )


@redteam_router.get(
    "/redteam/suites", response_model=RedteamSuitesResponse, tags=["platform"]
)
async def redteam_suites(
    auth: AuthContext = Depends(require_redteam_reader),
) -> RedteamSuitesResponse:
    """Return the battery catalogue, with the cost of running each suite live.

    The counts are read off the battery itself rather than restated here, so a probe
    added to :data:`aegis.redteam.battery.ATTACK_BATTERY` shows up in the picker with
    no edit on this side. ``semanticOnly`` and ``beyondRails`` are the honest columns,
    and they are two columns because they promise different things: a semantic-only
    probe has no deterministic signature and will appear as a leak in an *offline* run,
    while a ``beyondRails`` probe — an extraction sweep paced under the query-pattern
    monitor's window — leaks in every run there is, because nothing here is asked about
    it. A single column would let a reader conclude that wiring a completer closes both.
    """
    rows: list[RedteamSuiteRow] = []
    for suite in SUITES:
        probes = battery_for(suite)
        attacks = [a for a in probes if a.expects is Expectation.BLOCK]
        stages: dict[str, int] = {}
        for probe in probes:
            stages[probe.stage.value] = stages.get(probe.stage.value, 0) + 1
        rows.append(
            RedteamSuiteRow(
                id=suite.id,
                title=suite.title,
                summary=suite.summary,
                owasp=list(suite.owasp),
                categories=[c.value for c in suite.categories],
                attacks=len(attacks),
                controls=len(probes) - len(attacks),
                semantic_only=sum(1 for a in attacks if a.needs_llm),
                beyond_rails=sum(1 for a in attacks if a.beyond_rails),
                stages=stages,
                offline_floor=suite.offline_floor,
                live_floor=suite.live_floor,
                offline=_price(estimate_run(probes, live=False)),
                live=_price(estimate_run(probes, live=True)),
            )
        )
    may_run = _may_start_runs(auth)
    return RedteamSuitesResponse(
        suites=rows,
        default_suite=DEFAULT_SUITE_ID,
        may_run=may_run,
        may_run_live=may_run,
        refusal=None if may_run else _NOT_PLATFORM_STAFF,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Running the battery
# ─────────────────────────────────────────────────────────────────────────────


def _row(summary: RunSummary) -> RedteamRunRow:
    """Project a stored run's summary onto the wire."""
    return RedteamRunRow(
        run_id=summary.run_id,
        tenant_id=summary.tenant_id,
        suite=summary.suite,
        mode=summary.mode,
        started_at=summary.started_at.isoformat() if summary.started_at else None,
        duration_ms=summary.duration_ms,
        initiated_by=summary.initiated_by,
        attacks_total=summary.attacks_total,
        attacks_blocked=summary.attacks_blocked,
        attacks_unchecked=summary.attacks_unchecked,
        block_rate=summary.block_rate,
        controls_total=summary.controls_total,
        false_positives=summary.false_positives,
        false_positive_rate=summary.false_positive_rate,
        min_block_rate=summary.min_block_rate,
        max_false_positive_rate=summary.max_false_positive_rate,
        passed=summary.passed,
        estimated_cost_usd=summary.estimated_cost_usd,
    )


def _target_tenant(req: RedteamRunRequest, auth: AuthContext) -> int | None:
    """Resolve which tenant the run is against — and whose budget it spends.

    Platform staff may name any tenant (or none, for a platform-scoped run of Aegis's
    own rails). Anyone else is pinned to their own; the id is never taken from the
    request for a principal that could not have widened its own scope another way.
    """
    if not auth.is_platform_staff():
        return auth.tenant_id
    return req.tenant_id


async def _rails_for(*, live: bool) -> Rails:
    """Build the five stage checkers this run will drive.

    ``app.guardrails.tenant_pipeline`` folds the **bound tenant's** four
    ``guardrails.*`` settings onto the platform floor, so a run against a tenant
    exercises the rails that tenant actually enforces rather than the platform's
    defaults dressed up as theirs. Offline the same pipeline runs with no completer,
    which is what makes an offline run free and deterministic.
    """
    from app.guardrails import tenant_pipeline

    pipeline = await tenant_pipeline(live=live)

    async def screen_input(text: str, *, completer: object = None) -> Any:  # noqa: ANN401
        return await pipeline.check_input(text)

    async def screen_output(text: str, *, completer: object = None) -> Any:  # noqa: ANN401
        return await pipeline.check_output(text)

    async def screen_tool_result(text: str, *, completer: object = None) -> Any:  # noqa: ANN401
        return await pipeline.check_tool_result(text, tool_name="redteam.probe")

    # The write-time gate is not part of the tenant's guardrail pipeline: it is pure
    # code with no policy and no completer, and a tenant cannot loosen it. So this one
    # is the runner's own adapter over ``validate_content`` rather than a closure over
    # ``pipeline``, and it behaves identically live and offline. The same is true of the
    # query-pattern monitor, for the same two reasons and one more: it screens a *burst*
    # rather than a payload, and each probe gets its own throwaway monitor so a red-team
    # run can never push a real principal's window towards a threshold.
    return Rails(
        check_input=screen_input,
        check_output=screen_output,
        check_tool_result=screen_tool_result,
        check_ingest=check_ingest,
        check_sequence=check_sequence,
    )


async def _admit(tenant_id: int | None, user_id: int | None) -> None:
    """Refuse a live run that a tenant's budget cannot pay for, before it starts.

    The same :func:`aegis.governance.enforce_governance` the gateway chokepoint calls,
    invoked **up front** rather than relied on to trip partway through: a battery that
    dies on probe 19 of 36 has already spent the money and produced a report whose
    block rate is over a truncated denominator. A 429 with the reason is the honest
    outcome, and it names the ``budget`` gate exactly as the job substrate does.

    Delegated to :func:`app.api.routes.refuse_if_over_budget` since task 9.6, so that
    "exactly as" is enforced by there being one implementation rather than two that
    happen to agree today.
    """
    from app.api.routes import refuse_if_over_budget

    await refuse_if_over_budget(
        tenant_id=tenant_id,
        user_id=user_id,
        because=(
            "A live red-team run drives dozens of model calls, so it is refused rather "
            "than started on a budget that cannot finish it."
        ),
    )


@redteam_router.post(
    "/redteam/runs",
    response_model=RedteamRunDetailResponse,
    tags=["platform"],
    responses={429: {"description": "The target tenant is at a budget cap."}},
)
async def redteam_start_run(
    req: RedteamRunRequest,
    auth: AuthContext = Depends(require_redteam_operator),
) -> RedteamRunDetailResponse:
    """Run a suite, persist the report, and return it beside the previous run.

    Every parameter :func:`aegis.redteam.runner.run_redteam` accepts is on the wire:
    the battery (via ``suite``), both thresholds, and the completer (via ``mode``).

    A ``live`` run binds the target tenant's governance context first, so the model
    calls the guardrail layers make are budget-enforced and land in the usage ledger
    like any other spend, and is refused up front with a 429 when that tenant is
    already at a cap.

    Raises:
        HTTPException: 400 for an unknown suite or mode, 403 when the caller may not
            start runs, 429 when the target tenant's budget cannot pay for a live run,
            503 when the report cannot be persisted.
    """
    mode = req.mode.strip().lower()
    if mode not in (_OFFLINE, _LIVE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown run mode {req.mode!r}. Use 'offline' or 'live'.",
        )
    try:
        suite = suite_for(req.suite)
    except UnknownSuiteError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    live = mode == _LIVE
    probes = battery_for(suite)
    estimate = _price(estimate_run(probes, live=live))
    thresholds = RedTeamThresholds(
        min_block_rate=(
            req.min_block_rate
            if req.min_block_rate is not None
            else (suite.live_floor if live else suite.offline_floor)
        ),
        max_false_positive_rate=(
            req.max_false_positive_rate if req.max_false_positive_rate is not None else 0.0
        ),
    )

    tenant_id = _target_tenant(req, auth)
    if live:
        await _admit(tenant_id, auth.user_id)

    governance = GovernanceContext(tenant_id=tenant_id, user_id=auth.user_id, role=auth.role)
    token = set_governance_context(governance)
    started = time.monotonic()
    try:
        rails = await _rails_for(live=live)
        report = await run_redteam(battery=probes, thresholds=thresholds, rails=rails)
    finally:
        reset_governance_context(token)
    duration_ms = int((time.monotonic() - started) * 1000)

    payload = report.as_dict()
    payload["suite"] = suite.id
    payload["mode"] = mode
    run_id = f"rt-{uuid.uuid4().hex[:16]}"
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            row = await record_run(
                session,
                run_id=run_id,
                tenant_id=tenant_id,
                suite=suite.id,
                mode=mode,
                duration_ms=duration_ms,
                initiated_by=auth.username,
                initiated_role=auth.fine_role,
                report=payload,
                estimated_cost_usd=estimate.cost_usd,
            )
            prior = await previous_run(session, row, tenant_id=tenant_id)
            summary = RunSummary.of(row)
            await session.commit()
    except SQLAlchemyError as exc:
        logger.error("Red-team run %s could not be persisted.", run_id, exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The red-team battery ran but the report could not be stored, so there "
                "is no durable evidence of it. Nothing is returned rather than a "
                "result nobody can reproduce."
            ),
        ) from exc

    await _safe_audit(
        "redteam.run",
        auth,
        payload={
            "run_id": run_id,
            "suite": suite.id,
            "mode": mode,
            "target_tenant_id": tenant_id,
            "attacks_total": report.attacks_total,
            "attacks_blocked": report.attacks_blocked,
            "attacks_unchecked": report.attacks_unchecked,
            "block_rate": round(report.block_rate, 4),
            "false_positive_rate": round(report.false_positive_rate, 4),
            "passed": report.passed,
            "estimated_cost_usd": estimate.cost_usd,
        },
        tenant_id=tenant_id,
    )
    return RedteamRunDetailResponse(
        run=_row(summary),
        report=payload,
        previous=_row(prior) if prior is not None else None,
        estimate=estimate,
    )


# ─────────────────────────────────────────────────────────────────────────────
# History
# ─────────────────────────────────────────────────────────────────────────────


@redteam_router.get(
    "/redteam/runs", response_model=RedteamHistoryResponse, tags=["platform"]
)
async def redteam_history(
    suite: str | None = Query(default=None, description="Restrict to one suite id."),
    limit: int = Query(default=25, ge=1, le=100),
    auth: AuthContext = Depends(require_redteam_reader),
) -> RedteamHistoryResponse:
    """Return this scope's red-team runs, newest first.

    A tenant admin sees their own tenant's runs and nothing else — the scope comes
    from :meth:`AuthContext.tenant_scope`, so a ``tenant_id`` query parameter could
    not widen it even if one existed.
    """
    tenant_id = _scope(auth)
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            rows = await list_runs(session, tenant_id=tenant_id, suite=suite, limit=limit)
    except SQLAlchemyError as exc:
        logger.error("Red-team history read failed.", exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The red-team history could not be read.",
        ) from exc
    return RedteamHistoryResponse(rows=[_row(r) for r in rows])


@redteam_router.get(
    "/redteam/runs/{run_id}", response_model=RedteamRunDetailResponse, tags=["platform"]
)
async def redteam_run_detail(
    run_id: str,
    auth: AuthContext = Depends(require_redteam_reader),
) -> RedteamRunDetailResponse:
    """Return one stored run in full, with the previous run of the same suite beside it.

    A run outside the caller's scope is a 404, not a 403: telling a tenant admin that
    a run id exists but belongs to somebody else is an enumeration oracle.
    """
    tenant_id = _scope(auth)
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            row = await load_run(session, run_id, tenant_id=tenant_id)
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No red-team run with that id in this scope.",
                )
            prior = await previous_run(session, row, tenant_id=tenant_id)
            summary = RunSummary.of(row)
            report = dict(row.report or {})
    except SQLAlchemyError as exc:
        logger.error("Red-team run %s could not be read.", run_id, exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The red-team run could not be read.",
        ) from exc
    return RedteamRunDetailResponse(
        run=_row(summary), report=report, previous=_row(prior) if prior is not None else None
    )


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target`` as real ``APIRoute`` objects.

    Idempotent, unlike its two siblings: a route already on ``target`` at the same
    path is skipped. This module is mounted from the composition root while
    :mod:`app.api.routes` is being edited elsewhere, and mounting twice would put a
    second, shadowed copy of every handler in the served table — which is invisible
    at runtime (the first match wins) and confusing in exactly the place the route
    coverage test reads.

    Args:
        target: The application's main router. Its ``routes`` list is extended in
            place — see :mod:`app.api.routes_console` for why this is a merge rather
            than ``include_router``.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    target.routes.extend(
        route
        for route in redteam_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
