"""Release-gate wiring — the REAL ``eval_fn`` + durable ``approval_enqueue`` seams.

:mod:`app.ops.release` is deliberately abstract: it takes an ``eval_fn`` (score a
candidate system prompt) and an ``approval_enqueue`` (stage a risky change for a human)
so it can be unit-tested offline. This module provides the *live* implementations the
``/ops/release`` endpoint injects, plus the staged-release inbox read/decide helpers that
close the loop.

Nothing here is decorative:

* :func:`make_eval_fn` returns a genuine regression scorer. For a bounded slice of the
  offline eval corpus it retrieves real context (the same hybrid retriever the CI gate
  uses), **generates an answer under the candidate ``system_prompt``**, and grades that
  answer with the reasoning-model judge. The returned number therefore depends on the
  prompt — a better prompt yields a better-graded answer — which is exactly what the eval
  gate needs to compare a draft against its baseline.
* :func:`enqueue_release_approval` writes a durable :class:`~app.data.models.Approval`
  row (``action="prompt_release"``), reusing the same approvals table the agent gate
  uses, so a staged release is a real pending inbox item — but with a synthetic
  ``run_id``/``thread_id`` so it is fully **decoupled** from the agent-run resume
  machinery (no checkpoint, no live socket).
* :func:`list_pending_releases` / :func:`decide_release` are the inbox read + resolve
  path: a decision calls :func:`app.ops.release.apply_release_decision` (promote or
  archive the draft) and flips the durable row terminal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.api.schemas import RiskLevel
from app.core.models import ModelRole
from app.data.models import Approval, ApprovalStatus, PromptVersion

logger = logging.getLogger(__name__)

#: Default number of seed cases scored per candidate. Scoring the whole corpus per
#: release candidate (retrieve + generate + judge for every case) is heavy; a small
#: bounded slice keeps a release affordable while remaining a genuine, prompt-dependent
#: signal (the prompt is the generation system message for every case scored).
DEFAULT_EVAL_SUBSET = 3

#: Map the release module's textual risk level onto the durable-row risk enum.
_RISK_MAP: dict[str, RiskLevel] = {
    "low": RiskLevel.LOW,
    "medium": RiskLevel.MEDIUM,
    "high": RiskLevel.HIGH,
}

#: The durable-approval ``action`` that marks a staged prompt release (vs an agent gate).
RELEASE_ACTION = "prompt_release"


def make_eval_fn(complete: Any, *, limit: int = DEFAULT_EVAL_SUBSET) -> Any:  # noqa: ANN401
    """Return an async ``eval_fn(system_prompt) -> float`` over the regression corpus.

    The returned scorer is genuinely prompt-dependent. For each of the first ``limit``
    :data:`~app.eval.corpus.SEED_CASES` it:

    1. retrieves context with the offline hybrid retriever
       (:func:`app.eval.harness.build_eval_retriever` — the same real fused pipeline the
       CI gate runs);
    2. **generates an answer under the candidate ``system_prompt``** via ``complete``
       (``ModelRole.GENERATION``); and
    3. grades that answer for groundedness + relevance with the reasoning-model judge
       (:func:`app.eval.judge.judge_answer`).

    The mean blended judge score is returned. Because ``system_prompt`` is the system
    message every answer is generated under, the score moves with the prompt — so
    :func:`app.ops.release.release` compares a *real* eval of the draft against a *real*
    eval of the baseline, never a constant.

    Args:
        complete: The chat-completion callable (``app.core.llm.complete`` in production;
            a fake in tests). Both the generation and the judge run through it.
        limit: How many seed cases to score per candidate (bounded for cost).

    Returns:
        An ``async (system_prompt: str) -> float`` in ``[0, 1]``.
    """
    from app.eval.corpus import SEED_CASES
    from app.eval.harness import build_eval_retriever
    from app.eval.judge import judge_answer

    cases = SEED_CASES[: max(1, limit)]

    async def eval_fn(system_prompt: str) -> float:
        total = 0.0
        graded = 0
        for case in cases:
            # A fresh retriever per case so a prior query can never semantic-hit a later.
            retriever = build_eval_retriever()
            result = await retriever.retrieve(case.query)
            context = result.answer_context
            generation = await complete(
                ModelRole.GENERATION,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"{case.query}\n\nContext:\n{context}"},
                ],
                temperature=0.0,
            )
            answer = getattr(generation, "content", "") or ""
            verdict = await judge_answer(case.query, context, answer, complete=complete)
            total += (verdict.groundedness + verdict.relevance) / 2.0
            graded += 1
        return total / graded if graded else 0.0

    return eval_fn


async def enqueue_release_approval(
    *,
    prompt_key: str,
    draft_version_id: int,
    risk: str,
    reason: str,
    tenant_id: int | None = None,
) -> str:
    """Persist a durable ``PENDING`` approval for a staged prompt release; return its id.

    Reuses the :class:`~app.data.models.Approval` table (``action="prompt_release"``) so a
    staged release is a first-class, restart-surviving inbox item resolved by
    :func:`decide_release`. It is intentionally **decoupled** from the agent-run resume
    machinery: the synthetic ``run_id``/``thread_id`` (``prompt_release:<draft_id>``) is
    not a LangGraph thread, so no checkpoint or live socket is ever involved.

    This is the ``approval_enqueue`` injected into :func:`app.ops.release.release`.

    Args:
        prompt_key: The prompt key the draft belongs to.
        draft_version_id: The staged draft awaiting a human decision.
        risk: The release module's risk level (``"low"``/``"medium"``/``"high"``).
        reason: The one-line staging rationale (shown in the inbox).
        tenant_id: Optional owning tenant, for inbox scoping.

    Returns:
        The new approval's id.
    """
    from app.data.approvals import enqueue_approval

    approval_id = uuid4().hex
    synthetic = f"{RELEASE_ACTION}:{draft_version_id}"
    await enqueue_approval(
        approval_id=approval_id,
        run_id=synthetic,
        thread_id=synthetic,
        action=RELEASE_ACTION,
        args={"draft_version_id": draft_version_id, "prompt_key": prompt_key},
        risk=_RISK_MAP.get(str(risk).lower(), RiskLevel.MEDIUM),
        rationale=reason,
        tenant_id=tenant_id,
    )
    return approval_id


def _iso(ts: datetime | None) -> str | None:
    """Render a (possibly naive) timestamp as an ISO 8601 UTC string, or ``None``."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class PendingRelease:
    """A staged prompt release awaiting a human decision (a projected inbox row)."""

    approval_id: str
    prompt_key: str | None
    draft_version_id: int | None
    risk: str
    reason: str | None
    created_at: str | None


async def list_pending_releases(
    *, limit: int = 50, tenant_id: int | None = None
) -> list[PendingRelease]:
    """Return the ``PENDING`` staged prompt-release approvals, newest first.

    Args:
        limit: Maximum rows to return.
        tenant_id: When given, restrict to this tenant's rows.

    Returns:
        The staged releases as :class:`PendingRelease` projections.
    """
    from app.data.session import get_sessionmaker, set_tenant_scope

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        stmt = (
            select(Approval)
            .where(
                Approval.status == ApprovalStatus.PENDING,
                Approval.action == RELEASE_ACTION,
            )
            .order_by(Approval.created_at.desc(), Approval.id.desc())
            .limit(limit)
        )
        if tenant_id is not None:
            stmt = stmt.where(Approval.tenant_id == tenant_id)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            PendingRelease(
                approval_id=r.id,
                prompt_key=(r.args or {}).get("prompt_key"),
                draft_version_id=(r.args or {}).get("draft_version_id"),
                risk=r.risk.value if hasattr(r.risk, "value") else str(r.risk),
                reason=r.rationale,
                created_at=_iso(r.created_at),
            )
            for r in rows
        ]


@dataclass(frozen=True)
class ReleaseDecision:
    """The outcome of resolving a staged release."""

    approval_id: str
    approved: bool
    outcome: str  # "promoted" | "archived" | "unknown"
    prompt_key: str | None
    active_version: int | None


async def decide_release(
    *, approval_id: str, approved: bool, decided_by: str | None = None
) -> ReleaseDecision | None:
    """Resolve a staged release: promote or archive the draft, flip the durable row.

    Loads the ``prompt_release`` approval, applies the human decision to the draft via
    :func:`app.ops.release.apply_release_decision` (promote on approve, archive on
    reject), then marks the durable row terminal. Decoupled from the agent resume path —
    no checkpoint is ever touched.

    Args:
        approval_id: The staged-release approval to resolve.
        approved: ``True`` to promote the draft live, ``False`` to archive it.
        decided_by: Who decided (recorded on the durable row).

    Returns:
        A :class:`ReleaseDecision`, or ``None`` when the approval id is unknown.
    """
    from app.data.session import get_sessionmaker
    from app.ops import registry
    from app.ops.release import apply_release_decision

    async with get_sessionmaker()() as session:
        row = await session.get(Approval, approval_id)
        if row is None or row.action != RELEASE_ACTION:
            return None
        args = row.args or {}
        draft_version_id = args.get("draft_version_id")
        prompt_key = args.get("prompt_key")

        pv: PromptVersion | None = None
        if draft_version_id is not None:
            pv = await apply_release_decision(
                session, draft_version_id=int(draft_version_id), approved=approved
            )

        row.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        row.decided_at = datetime.now(UTC)
        row.decided_by = decided_by
        await session.commit()

        active_version: int | None = None
        if approved and pv is not None:
            active_version = pv.version
        elif approved and prompt_key is not None:
            active = await registry.get_active(session, str(prompt_key))
            active_version = active.version if active is not None else None

        outcome = (
            "promoted" if (approved and pv is not None)
            else "archived" if (not approved and pv is not None)
            else "unknown"
        )
        return ReleaseDecision(
            approval_id=approval_id,
            approved=approved,
            outcome=outcome,
            prompt_key=str(prompt_key) if prompt_key is not None else None,
            active_version=active_version,
        )
