"""The audit-log writer — the system's first-class accountability trail.

Every autonomous or approved action passes through :func:`record_audit`, which
persists who did what, with which model, under which trace, and who approved it.
It opens its own short-lived session (from the injected factory) so callers (agent
nodes, tool executors) can log without threading a session through their signatures.

The session factory and ``set_tenant_scope`` are injected via :func:`configure_audit`
(the host wires them at startup, mirroring :mod:`aegis.governance.enforcement`).

:func:`list_recent_audit` is the read side, and it **filters in SQL** (§7.11). A filter
applied to the page of rows a ``limit`` already returned is not a filter: it narrows what
you can see out of what you already fetched, so "what did this actor do last Tuesday"
answers "nothing" whenever last Tuesday fell off the end of the page. Every predicate
here is therefore a ``WHERE`` clause, and ``tenant_id`` is one of them — the caller
passes the scope it resolved from the sealed :class:`AuthContext`, never a client field.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select

from aegis.governance.chain import (
    GENESIS,
    canonical_payload,
    chain_hash,
    row_fingerprint,
)
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.governance.context import get_governance_context
from aegis.governance.models import AuditLog
from aegis.governance.rls import set_tenant_scope as _default_set_tenant_scope
from aegis.governance.types import AuditLogRow

__all__ = [
    "AUDIT_OUTCOMES",
    "classify_outcome",
    "configure_audit",
    "list_recent_audit",
    "record_audit",
]

_SessionFactory = Callable[[], AsyncSession]
_SetTenantScope = Callable[[AsyncSession, int | None], Awaitable[None]]

_session_factory: _SessionFactory | None = None
_set_tenant_scope: _SetTenantScope = _default_set_tenant_scope


def configure_audit(
    *,
    session_factory: _SessionFactory | None = None,
    set_tenant_scope: _SetTenantScope | None = None,
) -> None:
    """Wire the injected session factory and (optionally) ``set_tenant_scope``.

    Args:
        session_factory: A zero-arg callable returning an :class:`AsyncSession`.
        set_tenant_scope: The RLS scope binder; defaults to the package's own.
    """
    global _session_factory, _set_tenant_scope
    if session_factory is not None:
        _session_factory = session_factory
    if set_tenant_scope is not None:
        _set_tenant_scope = set_tenant_scope


def _session() -> AsyncSession:
    """Return a fresh :class:`AsyncSession` from the injected factory."""
    if _session_factory is None:
        raise RuntimeError(
            "aegis.governance audit is not configured; call "
            "configure_audit(session_factory=...) at startup."
        )
    return _session_factory()


async def record_audit(
    *,
    action: str,
    actor: str | None,
    model: str | None,
    trace_id: str | None,
    payload: dict[str, Any],
    approved_by: str | None = None,
    tenant_id: int | None = None,
) -> None:
    """Persist one audit record, attributed to the acting tenant when known (H2).

    Args:
        action: The action performed (e.g. ``"tool:create_ticket"``).
        actor: The principal that initiated the action, if known.
        model: The model deployment id involved, if any.
        trace_id: The OTel trace id (hex) correlating this action to its spans.
        payload: Structured details of the action (arguments, result summary).
        approved_by: The human who approved the action at the HITL gate, if any.
        tenant_id: The owning tenant; when omitted it is taken from the per-request
            governance context (``None`` for platform-scoped/ungoverned actions).
    """
    if tenant_id is None:
        gov = get_governance_context()
        tenant_id = gov.tenant_id if gov is not None else None
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)

        # ── the chain link ───────────────────────────────────────────────────────
        # Read the tail of THIS tenant's chain inside the same transaction as the
        # insert, so two concurrent writers cannot both read the same predecessor and
        # both claim it. If they race anyway, the unique index on
        # (tenant_id, prev_hash) turns the loser into an integrity error rather than a
        # silent fork — the failure mode this design exists to make impossible.
        tail = (
            await session.execute(
                select(AuditLog.row_hash)
                .where(
                    AuditLog.tenant_id.is_(None)
                    if tenant_id is None
                    else AuditLog.tenant_id == tenant_id
                )
                .where(AuditLog.row_hash.is_not(None))
                .order_by(AuditLog.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        # Supplied here rather than by ``func.now()``: it is part of what gets hashed,
        # and the database assigns its own value only after the app must already have
        # computed the hash. See the note on ``AuditLog``.
        ts = datetime.now(UTC).replace(tzinfo=None)

        # Store the CANONICAL payload, not the dict we were handed. ``jsonb`` does not
        # round-trip the text it is given — it drops key order and renormalises numbers
        # — so hashing what we sent and verifying what comes back are different
        # functions of the same data unless what we store is already a fixed point.
        # Skipping this makes a row nobody touched fail verification, and a verifier
        # that cries wolf is a verifier that gets turned off.
        stored_payload = json.loads(canonical_payload(payload))

        prev = tail if tail is not None else GENESIS
        row_hash = chain_hash(
            prev,
            row_fingerprint(
                tenant_id=tenant_id,
                ts=ts,
                action=action,
                actor=actor,
                model=model,
                trace_id=trace_id,
                payload=stored_payload,
                approved_by=approved_by,
            ),
        )

        session.add(
            AuditLog(
                tenant_id=tenant_id,
                ts=ts,
                action=action,
                actor=actor,
                model=model,
                trace_id=trace_id,
                payload=stored_payload,
                approved_by=approved_by,
                row_hash=row_hash,
                prev_hash=prev,
            )
        )
        await session.commit()


async def chain_row(
    session: AsyncSession,
    *,
    tenant_id: int | None,
    ts: datetime,
    action: str,
    actor: str | None,
    model: str | None = None,
    trace_id: str | None = None,
    payload: dict[str, Any] | None = None,
    approved_by: str | None = None,
) -> AuditLog:
    """Build a chained :class:`AuditLog` inside a caller's own transaction.

    ``record_audit`` opens its own session, which is exactly wrong for a writer that
    must commit its audit row *with* the state change it describes — the SLA sweeper
    writes the rejection and its record together precisely so the two can never
    disagree. That writer was therefore inserting ``AuditLog`` directly, and inserting
    it **unchained**: a row with no hash sitting mid-chain, which the verifier can only
    report as uncovered.

    This gives such a writer the chain without taking away its transaction. The tail is
    read in the caller's session, so the link is computed against what that transaction
    can see.

    Returns:
        The row, chained and ready to ``session.add``. Not added here — the caller owns
        the transaction and the ordering within it.
    """
    tail = (
        await session.execute(
            select(AuditLog.row_hash)
            .where(
                AuditLog.tenant_id.is_(None)
                if tenant_id is None
                else AuditLog.tenant_id == tenant_id
            )
            .where(AuditLog.row_hash.is_not(None))
            .order_by(AuditLog.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    stored_payload = json.loads(canonical_payload(payload))
    prev = tail if tail is not None else GENESIS
    return AuditLog(
        tenant_id=tenant_id,
        ts=ts,
        action=action,
        actor=actor,
        model=model,
        trace_id=trace_id,
        payload=stored_payload,
        approved_by=approved_by,
        row_hash=chain_hash(
            prev,
            row_fingerprint(
                tenant_id=tenant_id,
                ts=ts,
                action=action,
                actor=actor,
                model=model,
                trace_id=trace_id,
                payload=stored_payload,
                approved_by=approved_by,
            ),
        ),
        prev_hash=prev,
    )


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """What walking one tenant's audit chain found.

    Attributes:
        checked: Rows that carried a hash and were re-derived.
        unchained: Rows that predate the chain. Reported, never counted as verified —
            nothing can prove anything about history nobody hashed, and quietly folding
            these into a pass would be the exact overclaim this feature exists to end.
        intact: Whether every checked row re-derived to the hash it carries, in order.
        broken_at: The ``id`` of the first row that did not, or ``None``.
        detail: One sentence naming what broke, for an operator who is not going to
            read the algorithm.
        head: The last row's hash — this chain's current tip.

            **Why this is on the response.** A chain detects an edited row and a row
            removed from the middle, because both orphan everything downstream. It
            cannot, by itself, detect rows removed from the **end**: truncate the tail
            and what remains is a shorter chain that verifies perfectly. Nothing inside
            the database can close that, because the evidence of what was there is what
            was deleted.

            Publishing the head is what closes it, and it has to be closed *outside*.
            An operator who records this value — in a ticket, a monitor, another system
            — can detect truncation by noticing the head went backwards. Until that
            anchor exists somewhere else, tail truncation is an honest and stated limit
            of this verifier rather than a claim it quietly fails to make.
    """

    checked: int
    unchained: int
    intact: bool
    broken_at: int | None = None
    detail: str = ""
    head: str | None = None


async def verify_audit_chain(tenant_id: int | None) -> ChainVerification:
    """Walk one tenant's chain and report the first break, if any.

    The chain is what makes deletion detectable. Per-row hashes alone prove no row was
    *edited*; they say nothing about a row being removed, because the survivors still
    verify individually. Seeding each hash with its predecessor's means removing any row
    breaks every row after it.

    Args:
        tenant_id: The tenant whose chain to walk, or ``None`` for the platform chain.

    Returns:
        A :class:`ChainVerification`. ``intact`` is about the **checked** rows only;
        ``unchained`` is reported separately and on purpose.
    """
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.tenant_id.is_(None)
                        if tenant_id is None
                        else AuditLog.tenant_id == tenant_id
                    )
                    .order_by(AuditLog.id.asc())
                )
            )
            .scalars()
            .all()
        )

    unchained = sum(1 for r in rows if r.row_hash is None)
    chained = [r for r in rows if r.row_hash is not None]

    previous: str | None = None
    for row in chained:
        expected_prev = previous if previous is not None else GENESIS
        if row.prev_hash != expected_prev:
            return ChainVerification(
                checked=len(chained),
                unchained=unchained,
                intact=False,
                broken_at=row.id,
                detail=(
                    f"row {row.id} claims a predecessor this chain does not have — "
                    "a row before it was removed, or the trail was spliced."
                ),
            )
        recomputed = chain_hash(
            row.prev_hash,
            row_fingerprint(
                tenant_id=row.tenant_id,
                ts=row.ts,
                action=row.action,
                actor=row.actor,
                model=row.model,
                trace_id=row.trace_id,
                payload=row.payload,
                approved_by=row.approved_by,
            ),
        )
        if recomputed != row.row_hash:
            return ChainVerification(
                checked=len(chained),
                unchained=unchained,
                intact=False,
                broken_at=row.id,
                detail=f"row {row.id} does not hash to the value it carries — it was edited.",
            )
        previous = row.row_hash

    return ChainVerification(
        checked=len(chained),
        unchained=unchained,
        intact=True,
        head=chained[-1].row_hash if chained else None,
        detail=(
            f"{len(chained)} row(s) re-derived in order"
            + (
                f"; {unchained} row(s) predate the chain and are not covered by it."
                if unchained
                else "."
            )
        ),
    )


def _iso_utc(ts: datetime) -> str:
    """Render a (possibly naive) timestamp as an ISO 8601 UTC string.

    Timestamps stored via ``func.now()`` may come back naive; they are treated as
    UTC so the wire format is unambiguous for the admin audit view.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


#: The two outcomes an audit row can be classified into. There is no verdict column on
#: ``audit_log`` — the action name is the only honest signal — so this stays a
#: classification of the action and is never dressed up as a recorded result.
AUDIT_OUTCOMES: tuple[str, ...] = ("blocked", "completed")

#: An action whose name starts with one of these was a refusal by the rails.
_BLOCKED_PREFIXES: tuple[str, ...] = ("guardrail",)

#: An action whose name *contains* one of these was a refusal somewhere else (a denied
#: tool call, a blocked upload).
_BLOCKED_SUBSTRINGS: tuple[str, ...] = ("block", "denied")


def classify_outcome(action: str) -> str:
    """Return ``"blocked"`` or ``"completed"`` for an action name.

    The one definition of the outcome word. It is derived, not recorded: the trail has
    no verdict column, so a row is "blocked" only when the action itself says so, and
    everything else reads "completed" rather than inventing a state. The SQL predicate
    :func:`_outcome_clause` filters by the *same* rule, and a test holds the two to the
    same answer on the same actions — two spellings of one classification that drift
    apart would put a different word on the screen than the filter selected on.
    """
    lowered = action.lower()
    if lowered.startswith(_BLOCKED_PREFIXES):
        return "blocked"
    if any(token in lowered for token in _BLOCKED_SUBSTRINGS):
        return "blocked"
    return "completed"


def _outcome_clause(outcome: str) -> Any:  # noqa: ANN401 - a SQLAlchemy clause
    """Return the SQL form of :func:`classify_outcome` for ``outcome``."""
    lowered = func.lower(AuditLog.action)
    blocked = lowered.like(f"{_BLOCKED_PREFIXES[0]}%")
    for prefix in _BLOCKED_PREFIXES[1:]:
        blocked = blocked | lowered.like(f"{prefix}%")
    for token in _BLOCKED_SUBSTRINGS:
        blocked = blocked | lowered.like(f"%{token}%")
    return blocked if outcome == "blocked" else ~blocked


def _escape_like(value: str) -> str:
    """Neutralise the ``LIKE`` metacharacters in a user-typed value.

    Without this, ``%`` typed into the action box matches every action and the filter
    silently returns the whole trail — the most confusing possible answer to "show me
    only this".
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _naive_utc(ts: datetime) -> datetime:
    """Normalise a bound to naive UTC — the form ``audit_log.ts`` is stored in.

    ``ts`` is ``TIMESTAMP WITHOUT TIME ZONE`` holding UTC, so comparing it against an
    aware bound raises on PostgreSQL. An aware bound is converted; a naive one is
    trusted to already be UTC, which is what every writer in this package produces.
    """
    return ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo is not None else ts


async def list_recent_audit(
    limit: int = 50,
    *,
    tenant_id: int | None = None,
    actor: str | None = None,
    action_prefix: str | None = None,
    model: str | None = None,
    trace_id: str | None = None,
    outcome: str | None = None,
    query: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[AuditLogRow]:
    """Return the most recent audit rows, newest first, tenant-scoped and filtered (H2).

    Opens its own short-lived session (like :func:`record_audit`) so read-only
    callers need not thread a session through their signatures.

    **Every filter is a SQL predicate, applied before the ``LIMIT``.** That is the whole
    point of §7.11: filtering the page the limit already returned would answer "no such
    event" for anything older than the page, which is the same answer it gives for an
    event that never happened.

    Args:
        limit: Maximum number of rows to return (caller should clamp to a sane max).
        tenant_id: App-scope the trail to one tenant (belt-and-suspenders over RLS);
            ``None`` returns every tenant's rows (the platform-admin view). The caller
            resolves this from its sealed scope, never from a client field.
        actor: Exact actor match — the principal that initiated the action.
        action_prefix: Match actions starting with this string. Actions are namespaced
            (``ops.diagnose``, ``tool:create_ticket``, ``guardrail.block``), so a prefix
            selects a family; the value is escaped, so ``%`` in it is a literal ``%``.
        model: Exact model-deployment match.
        trace_id: Exact trace match — every row belonging to one run, which is the
            subject an operator actually chases an incident by.
        query: Case-insensitive substring across action, actor, model, trace and
            approver — the free-text box, made real. It used to run in the browser over
            the fetched page, which is why it could not find anything older than it.
        outcome: ``"blocked"`` or ``"completed"`` (see :func:`classify_outcome`). Any
            other value selects nothing rather than silently meaning "all".
        since: Lower bound on ``ts``, inclusive. Naive values are read as UTC.
        until: Upper bound on ``ts``, inclusive. Naive values are read as UTC.

    Returns:
        Up to ``limit`` :class:`~aegis.governance.types.AuditLogRow` records ordered by
        timestamp descending (ties broken by id descending), with ``ts`` serialised
        as an ISO 8601 UTC string.
    """
    async with _session() as session:
        # §9.5. This read used to bind no scope at all — it relied on the app-level
        # ``WHERE tenant_id`` below and on the policy's fail-open branch underneath it,
        # which means a forgotten predicate here would have returned every tenant's
        # trail with no error anywhere. The scope the caller resolved is bound, so the
        # database enforces the same answer the WHERE clause asks for; ``None`` is the
        # platform-admin view and now *says* so rather than being inferred from silence.
        await _set_tenant_scope(session, tenant_id)
        stmt = (
            select(AuditLog)
            .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
            .limit(limit)
        )
        if tenant_id is not None:
            stmt = stmt.where(AuditLog.tenant_id == tenant_id)
        if actor:
            stmt = stmt.where(AuditLog.actor == actor)
        if action_prefix:
            stmt = stmt.where(
                AuditLog.action.like(f"{_escape_like(action_prefix)}%", escape="\\")
            )
        if model:
            stmt = stmt.where(AuditLog.model == model)
        if trace_id:
            stmt = stmt.where(AuditLog.trace_id == trace_id)
        if query and query.strip():
            needle = f"%{_escape_like(query.strip())}%"
            stmt = stmt.where(
                or_(
                    AuditLog.action.ilike(needle, escape="\\"),
                    AuditLog.actor.ilike(needle, escape="\\"),
                    AuditLog.model.ilike(needle, escape="\\"),
                    AuditLog.trace_id.ilike(needle, escape="\\"),
                    AuditLog.approved_by.ilike(needle, escape="\\"),
                )
            )
        if outcome:
            stmt = (
                stmt.where(_outcome_clause(outcome))
                if outcome in AUDIT_OUTCOMES
                # An unknown word must not widen the read to everything: that is how a
                # typo turns a filtered view into the whole trail without saying so.
                else stmt.where(AuditLog.id.is_(None))
            )
        if since is not None:
            stmt = stmt.where(AuditLog.ts >= _naive_utc(since))
        if until is not None:
            stmt = stmt.where(AuditLog.ts <= _naive_utc(until))
        rows = (await session.execute(stmt)).scalars().all()
    return [
        AuditLogRow(
            id=row.id,
            ts=_iso_utc(row.ts),
            action=row.action,
            actor=row.actor,
            model=row.model,
            trace_id=row.trace_id,
            approved_by=row.approved_by,
            outcome=classify_outcome(row.action),
        )
        for row in rows
    ]
