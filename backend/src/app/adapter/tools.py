"""Action tools — the real, typed operations the agent may perform.

This is **piece 4 of 10** of the adapter. It exposes a small set of **MCP-shaped**
action tools over the service-request domain. Every tool is:

* **Typed** — arguments are validated by a pydantic model, and the MCP/OpenAI
  ``function`` schema is derived from that model (:meth:`ToolSpec.definition`), so
  the same definition drives both validation and the ``tools=`` payload passed to
  ``app.core.llm.complete``.
* **Idempotent** — re-running a tool with identical arguments converges to the
  same state; the second run reports ``changed=False``.
* **Reversible** — every result carries an :class:`InverseAction` (a tool name +
  args) that undoes it, enabling clean rollback / human-in-the-loop rejection.
* **Audited** — every invocation calls :func:`app.data.record_audit` (injected via
  :class:`ToolContext`), so there is an immutable trail of who did what.

Authorisation is **per-persona**: :data:`ALLOWLIST` maps a persona id to the set
of tools it may call, and :func:`run_tool` enforces it *before* any side effect.
This is the single source of truth the personas module reads from.

The data-layer dependencies (the record store and ``record_audit``) are injected
through :class:`ToolContext` so tools are unit-testable with no Postgres and no
network. In production the core wires the real store and audit sink in; the
lazy fallbacks here keep the module importable before those exist.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field

from app.adapter.schema import (
    CaseNote,
    Category,
    Priority,
    RequestStatus,
    ServiceRequest,
)
from app.api.schemas import RiskLevel

# ─────────────────────────────────────────────────────────────────────────────
# Injected data-layer contracts (structural — no hard import of app.data)
# ─────────────────────────────────────────────────────────────────────────────


class RecordStore(Protocol):
    """The minimal record access the tools need (a data-layer view)."""

    def get_request(self, request_id: str) -> ServiceRequest | None:
        """Return the request with ``request_id``, or None if absent."""
        ...

    def list_requests(self) -> list[ServiceRequest]:
        """Return every request this store holds, in a stable order.

        The read side of the seam. ``get_request`` can only answer a question that
        already knows the answer's id, so a store that offered nothing else made an
        id-taking tool the *only* thing a planner could reach — and a planner that is
        (correctly) forbidden from inventing ids could therefore reach nothing. This
        is the one accessor :func:`find_requests` narrows; filtering, ordering and the
        hard result cap all live in the tool, so a store never has to be trusted to
        bound anything.
        """
        ...

    def put_request(self, request: ServiceRequest) -> None:
        """Persist ``request`` (insert or replace by id)."""
        ...


class AuditFn(Protocol):
    """Structural view of ``app.data.record_audit``."""

    async def __call__(
        self,
        *,
        action: str,
        actor: str | None,
        model: str | None,
        trace_id: str | None,
        payload: dict,
        approved_by: str | None = None,
    ) -> None:
        """Append one immutable audit record."""
        ...


class InMemoryRecordStore:
    """A dict-backed :class:`RecordStore` for demos, seeding and tests."""

    def __init__(self, requests: list[ServiceRequest] | None = None) -> None:
        """Initialise the store, optionally seeded with ``requests``."""
        self._requests: dict[str, ServiceRequest] = {r.id: r for r in (requests or [])}

    @classmethod
    def from_dataset(cls, dataset: object) -> InMemoryRecordStore:
        """Build a store from a :class:`~app.adapter.schema.SyntheticDataset`.

        Args:
            dataset: An object exposing a ``requests`` iterable of
                :class:`ServiceRequest`.

        Returns:
            A populated in-memory store.
        """
        requests = list(getattr(dataset, "requests", []))
        return cls(requests)

    def get_request(self, request_id: str) -> ServiceRequest | None:
        """Return the request with ``request_id``, or None if absent."""
        return self._requests.get(request_id)

    def list_requests(self) -> list[ServiceRequest]:
        """Return all stored requests (public, ordered by insertion).

        A stable public accessor so callers (e.g. the ML feature builder) never
        reach into the private ``_requests`` mapping.
        """
        return list(self._requests.values())

    def put_request(self, request: ServiceRequest) -> None:
        """Persist ``request`` (insert or replace by id)."""
        self._requests[request.id] = request


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────


class UnknownToolError(KeyError):
    """Raised when a tool name is not in the registry."""


class ToolNotAllowedError(PermissionError):
    """Raised when a persona attempts a tool outside its allowlist."""


# ─────────────────────────────────────────────────────────────────────────────
# Results + execution context
# ─────────────────────────────────────────────────────────────────────────────


class InverseAction(BaseModel):
    """A (tool, args) pair that reverses a completed action."""

    tool: str
    args: dict


class ToolActionResult(BaseModel):
    """The typed outcome of running an action tool."""

    ok: bool = Field(description="Whether the action succeeded.")
    changed: bool = Field(description="Whether state actually changed (idempotency).")
    summary: str = Field(description="Human-readable, demoable result summary.")
    previous_state: dict = Field(
        default_factory=dict, description="Prior values, for audit / rollback."
    )
    inverse: InverseAction | None = Field(
        default=None, description="Action that undoes this one, if reversible."
    )


@dataclass
class ToolContext:
    """Everything a tool needs to run, injected by the caller.

    Attributes:
        store: The record store the tool reads and writes.
        actor: Persona/agent id performing the action (for the audit trail).
        model: Model id that proposed the action, if any.
        trace_id: Observability correlation id, if any.
        approved_by: Who approved the action at the HITL gate, if applicable.
        audit: The audit sink; defaults to ``app.data.record_audit`` at run time.
    """

    store: RecordStore
    actor: str | None = None
    model: str | None = None
    trace_id: str | None = None
    approved_by: str | None = None
    audit: AuditFn | None = field(default=None)


# ─────────────────────────────────────────────────────────────────────────────
# Argument models (drive both validation and the MCP tool schema)
# ─────────────────────────────────────────────────────────────────────────────


FIND_REQUESTS_DEFAULT_LIMIT = 10
"""Rows :func:`find_requests` returns when the caller does not ask for a number."""

FIND_REQUESTS_MAX_LIMIT = 25
"""Hard ceiling on :func:`find_requests`, enforced by the args model **and** the tool.

A lookup tool exists to hand the planner a *shortlist* to choose from, not a table
dump. Every row it returns is pasted verbatim into the next planning prompt, so an
unbounded limit is simultaneously a token bill, a context-window risk and a much
larger blast radius for the tool-result injection rail to screen. The ceiling is in
the pydantic model (so an over-large ``limit`` is a validation error the planner sees
and can correct) and re-applied in the body (so a caller that bypasses the model —
a future direct call, a test — still cannot exceed it).
"""


class FindRequestsArgs(BaseModel):
    """Arguments for :func:`find_requests` — every filter optional, all AND-ed."""

    status: RequestStatus | None = Field(
        default=None, description="Only requests in this lifecycle status."
    )
    priority: Priority | None = Field(
        default=None, description="Only requests at this priority."
    )
    category: Category | None = Field(
        default=None, description="Only requests in this subject area."
    )
    customer_id: str | None = Field(
        default=None, description="Only requests raised by this customer id."
    )
    assigned_agent_id: str | None = Field(
        default=None,
        description="Only requests assigned to this agent id. Use '' for unassigned.",
    )
    text: str | None = Field(
        default=None,
        description=(
            "Case-insensitive substring to match in the request id, title or "
            "description. Pass a known id here to confirm that request exists."
        ),
    )
    oldest_first: bool = Field(
        default=True,
        description="Order by creation time: oldest first (default), else newest first.",
    )
    limit: int = Field(
        default=FIND_REQUESTS_DEFAULT_LIMIT,
        ge=1,
        le=FIND_REQUESTS_MAX_LIMIT,
        description=f"Maximum rows to return (1–{FIND_REQUESTS_MAX_LIMIT}).",
    )


class UpdateStatusArgs(BaseModel):
    """Arguments for :func:`update_request_status`."""

    request_id: str = Field(description="Id of the request to update.")
    status: RequestStatus = Field(description="Target lifecycle status.")


class AssignArgs(BaseModel):
    """Arguments for :func:`assign_request`."""

    request_id: str = Field(description="Id of the request to (re)assign.")
    agent_id: str | None = Field(
        default=None, description="Agent id to assign, or null to unassign."
    )


class AddNoteArgs(BaseModel):
    """Arguments for :func:`add_case_note`."""

    request_id: str = Field(
        description=(
            "Id of the request to annotate, as returned by find_requests. Not a title, "
            "a document name or a phrase from the question."
        )
    )
    body: str = Field(min_length=1, description="Note text to append.")
    author: str | None = Field(default=None, description="Who wrote the note.")
    retract: bool = Field(
        default=False, description="If true, remove the matching note (the inverse)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────────────────────────────────────


async def _emit_audit(ctx: ToolContext, action: str, payload: dict) -> None:
    """Record one audit entry, resolving the sink lazily if not injected."""
    audit = ctx.audit
    if audit is None:
        try:  # app.data may not exist yet during parallel builds.
            from app.data import record_audit as audit  # noqa: PLC0415
        except ImportError:
            return  # Best-effort: never let a missing sink block the action.
    await audit(
        action=action,
        actor=ctx.actor,
        model=ctx.model,
        trace_id=ctx.trace_id,
        payload=payload,
        approved_by=ctx.approved_by,
    )


def _age_hours(request: ServiceRequest, *, now: datetime) -> float:
    """Return how many hours ago ``request`` was opened (0.0 if it is in the future).

    ``created_at`` is tz-aware in generated data and tz-naive in hand-built fixtures,
    so a naive value is read as UTC rather than being allowed to raise mid-lookup.
    """
    created = request.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return max(0.0, (now - created).total_seconds() / 3600.0)


def _matches(request: ServiceRequest, parsed: FindRequestsArgs) -> bool:
    """Return whether ``request`` satisfies every filter set on ``parsed``."""
    if parsed.status is not None and request.status is not parsed.status:
        return False
    if parsed.priority is not None and request.priority is not parsed.priority:
        return False
    if parsed.category is not None and request.category is not parsed.category:
        return False
    if parsed.customer_id is not None and request.customer_id != parsed.customer_id:
        return False
    if parsed.assigned_agent_id is not None:
        wanted = parsed.assigned_agent_id or None  # '' means "unassigned"
        if request.assigned_agent_id != wanted:
            return False
    if parsed.text:
        needle = parsed.text.casefold()
        # The **id** is in the haystack deliberately. A planner holding an id from an
        # earlier turn types it into the only free-text field the tool has; when that
        # searched prose alone, ``text="req-000029"`` matched nothing, the lookup
        # reported "no requests match", and the planner re-ran the identical call
        # rather than acting on a request that plainly exists. Observed live.
        haystack = f"{request.id}\n{request.title}\n{request.description}".casefold()
        if needle not in haystack:
            return False
    return True


def _describe(request: ServiceRequest, *, now: datetime) -> str:
    """Render one shortlist row: enough to choose between requests, and no more."""
    age = _age_hours(request, now=now)
    assignee = request.assigned_agent_id or "unassigned"
    breach = " SLA-BREACHED" if age > request.sla_hours else ""
    return (
        f"{request.id} | {request.status.value} | {request.priority.value} | "
        f"{request.category.value} | {assignee} | age {age:.0f}h of "
        f"{request.sla_hours:.0f}h SLA{breach} | {request.title}"
    )


async def find_requests(args: dict, ctx: ToolContext) -> ToolActionResult:
    """Look up service requests matching a filter (**read-only**, LOW risk).

    **Why this tool exists.** The three write tools all take a ``request_id``, and the
    persona prompt correctly forbids inventing one. With no read tool in the roster
    there was no legitimate way for the planner to *obtain* an id, so it could never
    propose a write — and the human-approval gate on ``update_request_status``, the
    platform's whole bounded-autonomy story, was unreachable from a plain question
    like "close the oldest resolved billing case". Observed directly: the planner
    tried to fabricate a lookup by calling ``load_skill('Service Request Lookup')``,
    a skill that does not exist, because that was the only read-shaped affordance it
    had. This tool is the missing lookup, and it deliberately does **not** lower any
    bar: it neither mutates nor gates, and the write it enables is still HIGH-risk and
    still stops at the human.

    **Read-only, and structurally so.** It calls exactly one store method —
    ``list_requests`` — and never ``put_request``, so there is no code path here that
    can change a record. It therefore has no ``previous_state`` and no ``inverse``:
    there is nothing to roll back. It still writes an audit row (who looked, with what
    filter, and which ids came back), because "read-only" is a statement about state,
    not about accountability.

    **Same data path as every other tool.** The store is the one the caller injected in
    :class:`ToolContext` — in production the single process-wide record store that
    ``update_request_status`` writes to and the MCP front door reads. No second
    connection, no second query, no privileged handle: whatever the platform's scoping
    of that store is, this tool inherits it exactly and can never see more than the
    tools already registered beside it.

    **Bounded.** Filters are AND-ed, results are ordered by creation time and truncated
    to :data:`FIND_REQUESTS_MAX_LIMIT` at the very latest point, so the row count is
    capped no matter what the planner asks for.

    Args:
        args: Raw arguments, validated against :class:`FindRequestsArgs`.
        ctx: The execution context (store, actor, audit sink).

    Returns:
        A :class:`ToolActionResult` whose ``summary`` is one line per matching request
        — id, status, priority, category, assignee, age against SLA, title. ``ok`` is
        False **only** when nothing matched: an empty shortlist is a round that did not
        advance the goal, and reporting it as such is what lets the graph's bounded
        self-repair loop widen the filter and try again rather than answering "none"
        from a filter that was simply too narrow. ``changed`` is always False.
    """
    parsed = FindRequestsArgs.model_validate(args)
    lister = getattr(ctx.store, "list_requests", None)
    if lister is None:
        # A store that predates the read side of the protocol. Loud in the summary and
        # never a silent empty result, which would read as "no such requests".
        return ToolActionResult(
            ok=False,
            changed=False,
            summary="This record store cannot enumerate requests (no list_requests).",
        )

    now = datetime.now(tz=UTC)
    matched = [r for r in lister() if _matches(r, parsed)]
    matched.sort(
        key=lambda r: (
            r.created_at.replace(tzinfo=UTC) if r.created_at.tzinfo is None
            else r.created_at
        ),
        reverse=not parsed.oldest_first,
    )
    total = len(matched)
    # Re-clamp in the body: the args model already refuses an over-large ``limit``, and
    # a direct caller that skipped it still cannot get more than the ceiling.
    page = matched[: min(parsed.limit, FIND_REQUESTS_MAX_LIMIT)]

    await _emit_audit(
        ctx,
        "find_requests",
        {
            "filter": parsed.model_dump(exclude_none=True, mode="json"),
            "matched": total,
            "returned": [r.id for r in page],
        },
    )

    if not page:
        return ToolActionResult(
            ok=False,
            changed=False,
            summary="No service requests match that filter. Try a broader one.",
        )

    header = (
        f"{len(page)} of {total} matching request(s) "
        "— id | status | priority | category | assignee | age/SLA | title:"
    )
    return ToolActionResult(
        ok=True,
        changed=False,
        summary="\n".join([header, *(_describe(r, now=now) for r in page)]),
    )


async def update_request_status(args: dict, ctx: ToolContext) -> ToolActionResult:
    """Set a request's lifecycle status (idempotent, reversible).

    Args:
        args: Raw arguments, validated against :class:`UpdateStatusArgs`.
        ctx: The execution context (store, actor, audit sink).

    Returns:
        A :class:`ToolActionResult`; its inverse sets the status back.
    """
    parsed = UpdateStatusArgs.model_validate(args)
    request = ctx.store.get_request(parsed.request_id)
    if request is None:
        return ToolActionResult(
            ok=False, changed=False, summary=f"No request {parsed.request_id!r}."
        )

    previous = request.status
    changed = previous != parsed.status
    if changed:
        ctx.store.put_request(
            request.model_copy(
                update={"status": parsed.status, "updated_at": datetime.now(tz=UTC)}
            )
        )
    await _emit_audit(
        ctx,
        "update_request_status",
        {"request_id": parsed.request_id, "from": previous.value, "to": parsed.status.value},
    )
    return ToolActionResult(
        ok=True,
        changed=changed,
        summary=f"Status {previous.value} → {parsed.status.value}"
        if changed
        else f"Already {parsed.status.value}",
        previous_state={"status": previous.value},
        inverse=InverseAction(
            tool="update_request_status",
            args={"request_id": parsed.request_id, "status": previous.value},
        ),
    )


async def assign_request(args: dict, ctx: ToolContext) -> ToolActionResult:
    """Assign (or unassign) a request to an agent (idempotent, reversible).

    Args:
        args: Raw arguments, validated against :class:`AssignArgs`.
        ctx: The execution context (store, actor, audit sink).

    Returns:
        A :class:`ToolActionResult`; its inverse restores the prior assignee.
    """
    parsed = AssignArgs.model_validate(args)
    request = ctx.store.get_request(parsed.request_id)
    if request is None:
        return ToolActionResult(
            ok=False, changed=False, summary=f"No request {parsed.request_id!r}."
        )

    previous = request.assigned_agent_id
    changed = previous != parsed.agent_id
    if changed:
        ctx.store.put_request(
            request.model_copy(
                update={"assigned_agent_id": parsed.agent_id, "updated_at": datetime.now(tz=UTC)}
            )
        )
    await _emit_audit(
        ctx,
        "assign_request",
        {"request_id": parsed.request_id, "from": previous, "to": parsed.agent_id},
    )
    return ToolActionResult(
        ok=True,
        changed=changed,
        summary=f"Assigned to {parsed.agent_id}" if changed else "Assignee unchanged",
        previous_state={"assigned_agent_id": previous},
        inverse=InverseAction(
            tool="assign_request",
            args={"request_id": parsed.request_id, "agent_id": previous},
        ),
    )


def _note_id(request_id: str, author: str, body: str) -> str:
    """Derive a deterministic note id from its content (dedupe key)."""
    digest = hashlib.sha1(f"{request_id}|{author}|{body}".encode()).hexdigest()  # noqa: S324
    return f"note-{digest[:12]}"


async def add_case_note(args: dict, ctx: ToolContext) -> ToolActionResult:
    """Append (or retract) a case note (idempotent, self-reversible).

    The note id is derived from its content, so adding the same note twice is a
    no-op. Passing ``retract=True`` removes that note — which is exactly the
    inverse of the add, making the tool its own undo.

    Args:
        args: Raw arguments, validated against :class:`AddNoteArgs`.
        ctx: The execution context (store, actor, audit sink).

    Returns:
        A :class:`ToolActionResult`; its inverse toggles the ``retract`` flag.
    """
    parsed = AddNoteArgs.model_validate(args)
    request = ctx.store.get_request(parsed.request_id)
    if request is None:
        return ToolActionResult(
            ok=False, changed=False, summary=f"No request {parsed.request_id!r}."
        )

    author = parsed.author or ctx.actor or "system"
    note_id = _note_id(parsed.request_id, author, parsed.body)
    present = any(n.id == note_id for n in request.notes)

    if parsed.retract:
        changed = present
        notes = [n for n in request.notes if n.id != note_id]
        summary = "Note retracted" if changed else "Note already absent"
    else:
        changed = not present
        notes = list(request.notes)
        if changed:
            notes.append(
                CaseNote(
                    id=note_id, author=author, body=parsed.body, created_at=datetime.now(tz=UTC)
                )
            )
        summary = "Note added" if changed else "Note already present"

    if changed:
        ctx.store.put_request(
            request.model_copy(update={"notes": notes, "updated_at": datetime.now(tz=UTC)})
        )
    await _emit_audit(
        ctx,
        "add_case_note",
        {"request_id": parsed.request_id, "note_id": note_id, "retract": parsed.retract},
    )
    return ToolActionResult(
        ok=True,
        changed=changed,
        summary=summary,
        previous_state={"note_present": present},
        inverse=InverseAction(
            tool="add_case_note",
            args={
                "request_id": parsed.request_id,
                "body": parsed.body,
                "author": author,
                "retract": not parsed.retract,
            },
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Registry + per-persona allowlist
# ─────────────────────────────────────────────────────────────────────────────


class ToolHandler(Protocol):
    """The async signature every action tool implements."""

    async def __call__(self, args: dict, ctx: ToolContext) -> ToolActionResult:
        """Run the tool against ``args`` in ``ctx``."""
        ...


@dataclass(frozen=True)
class ToolSpec:
    """A registered action tool: its metadata, schema and handler."""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler
    risk: RiskLevel
    read_only: bool = False
    """Whether a call cannot modify its environment at all (MCP ``readOnlyHint``).

    Asserted, never inferred from the risk tier: LOW risk means "cheap to get wrong",
    which is not the same claim as "changes nothing". ``add_case_note`` is LOW and
    writes; ``find_requests`` is LOW and does not. The default is the cautious reading,
    so a tool registered without thinking about it is advertised as a writer.
    """
    destructive: bool = False
    """Whether a call overwrites state a reader would miss (MCP ``destructiveHint``)."""
    idempotent: bool = False
    """Whether repeating the identical call converges (MCP ``idempotentHint``).

    These two are **per tool and asserted, never derived from the risk tier**, because
    risk does not imply idempotency: appending a note is low risk and not idempotent,
    while a gated status transition is high risk and is idempotent. They live here, on
    the tool, rather than in a name-keyed table in ``app.mcp`` — that table was core
    naming this domain's three tools, so a retarget silently lost every hint and the
    MCP surface advertised a conservative guess as an assertion. The defaults are the
    cautious reading, so a tool registered without thinking about it is never
    advertised as safer than it is.
    """

    def definition(self) -> dict:
        """Return the MCP/OpenAI ``function`` tool definition for the LLM.

        Returns:
            A dict of the form ``{"type": "function", "function": {...}}`` whose
            parameter schema is derived from :attr:`args_model`.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


#: Where a ``request_id`` is allowed to come from, stated on **every** tool that takes
#: one rather than only on the tool that produces them.
#:
#: The rule was written once, in ``find_requests``' description ("use this to obtain a
#: request id — never guess or invent one") — which is precisely where a model reaching
#: for a *write* tool never reads it. Asked a read-only question about an escalation
#: runbook, a four-agent fan-out had all four lanes call ``add_case_note`` with a
#: request id assembled out of the words in the question. Four writes attempted, four
#: "no such request", nothing learned between them.
#:
#: A constraint that only appears on the tool a model chose *not* to call is not a
#: constraint. This is appended to each write tool's own description so the rule arrives
#: with the tool the model is actually considering.
_ID_RULE = (
    "The request_id must be an id that find_requests returned — call it first and use "
    "one of its results. Never invent an id or assemble one from words in the question; "
    "an identifier that was not returned by a lookup does not name a real request and "
    "this call will fail against it. If no lookup has been run, run one instead of "
    "guessing."
)

TOOL_REGISTRY: dict[str, ToolSpec] = {
    "find_requests": ToolSpec(
        name="find_requests",
        description=(
            "Look up service requests on the desk by status, priority, category, "
            "customer, assignee or free text, newest- or oldest-first. Read-only: it "
            "changes nothing. Returns a short list of real request ids with the "
            "fields needed to choose between them (status, priority, category, "
            "assignee, age against SLA, title). Use this to obtain a request id — "
            "never guess or invent one."
        ),
        args_model=FindRequestsArgs,
        handler=find_requests,
        # LOW, and it is the one tool here for which that tier needs no argument: it
        # reads. It must stay below every deployment's gate_min_risk, because a lookup
        # that paused for a human would put the approval dialog in front of the step
        # that merely tells the planner which request the human meant — and the gate
        # this tool exists to make reachable is the one on the WRITE, which stays HIGH.
        risk=RiskLevel.LOW,
        read_only=True,
        # Reads nothing away and returns the same shortlist for the same filter over
        # unchanged records, so it is both non-destructive and idempotent.
        idempotent=True,
    ),
    "update_request_status": ToolSpec(
        name="update_request_status",
        description=(
            "Change the lifecycle status of a service request. " + _ID_RULE
        ),
        args_model=UpdateStatusArgs,
        handler=update_request_status,
        # HIGH: resolving/closing a customer request is a consequential,
        # customer-visible state change — it routes to the human-approval gate
        # (bounded autonomy). Assignment/notes are lower-consequence.
        risk=RiskLevel.HIGH,
        # Overwrites the prior status (destructive in the MCP sense), but setting the
        # same target status twice is one state.
        destructive=True,
        idempotent=True,
    ),
    "assign_request": ToolSpec(
        name="assign_request",
        description=(
            "Assign or unassign a service request to a support agent. " + _ID_RULE
        ),
        args_model=AssignArgs,
        handler=assign_request,
        risk=RiskLevel.MEDIUM,
        # Re-assigning to the same agent converges, and the previous assignee is
        # recoverable from the timeline.
        idempotent=True,
    ),
    "add_case_note": ToolSpec(
        name="add_case_note",
        description=(
            "Append (or retract) a note on a service request's timeline. " + _ID_RULE
        ),
        args_model=AddNoteArgs,
        handler=add_case_note,
        risk=RiskLevel.LOW,
        # Re-running appends a second note, so explicitly NOT idempotent.
    ),
}
"""Name → :class:`ToolSpec` for every action tool the domain exposes."""


ALLOWLIST: dict[str, frozenset[str]] = {
    # The operations lead (admin) may perform every action — and, since the registry
    # gained a read tool, look requests up. That persona's data scope is
    # ``ScopeKind.ALL`` ("every request on the desk"), so enumerating the desk grants
    # it nothing its scope did not already say it has.
    "operations_lead": frozenset(TOOL_REGISTRY),
    # The client (end user) may only annotate their own requests.
    #
    # **``find_requests`` is deliberately NOT here.** The client persona's scope is
    # ``ScopeKind.OWN`` on ``customer_id``, and that narrowing is applied by the
    # retrieval/data layers from the authenticated subject — a value
    # :class:`ToolContext` does not carry, so this module cannot enforce it. A tool
    # that takes ``customer_id`` as a *filter* would let a client enumerate any
    # customer's requests simply by passing someone else's id. Listing it for this
    # persona is therefore not a roster line, it is a scope change; it waits until the
    # authenticated subject reaches the tool layer and the filter can be pinned to it.
    "client": frozenset({"add_case_note"}),
}
"""Persona id → the set of tool names that persona is authorised to call."""


def is_allowed(persona_id: str, tool_name: str) -> bool:
    """Return whether ``persona_id`` may call ``tool_name``."""
    return tool_name in ALLOWLIST.get(persona_id, frozenset())


def tools_for(persona_id: str) -> list[ToolSpec]:
    """Return the :class:`ToolSpec` list a persona is allowed to use."""
    return [TOOL_REGISTRY[name] for name in sorted(ALLOWLIST.get(persona_id, frozenset()))]


def tool_definitions_for(persona_id: str) -> list[dict]:
    """Return the LLM ``tools=`` payload for a persona (allowlist-filtered)."""
    return [spec.definition() for spec in tools_for(persona_id)]


async def run_tool(
    persona_id: str, tool_name: str, args: dict, ctx: ToolContext
) -> ToolActionResult:
    """Authorise and execute a tool for a persona.

    The allowlist is checked **before** any side effect, so an unauthorised call
    can never mutate state or emit an audit record.

    Args:
        persona_id: The calling persona's id.
        tool_name: The tool to run.
        args: Raw tool arguments.
        ctx: The execution context.

    Returns:
        The tool's :class:`ToolActionResult`.

    Raises:
        UnknownToolError: If ``tool_name`` is not registered.
        ToolNotAllowedError: If ``persona_id`` is not allowed to call it.
    """
    if tool_name not in TOOL_REGISTRY:
        raise UnknownToolError(tool_name)
    if not is_allowed(persona_id, tool_name):
        raise ToolNotAllowedError(
            f"Persona {persona_id!r} may not call tool {tool_name!r}."
        )
    return await TOOL_REGISTRY[tool_name].handler(args, ctx)
