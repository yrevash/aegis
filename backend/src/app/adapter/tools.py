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

from app.adapter.schema import CaseNote, RequestStatus, ServiceRequest
from app.api.schemas import RiskLevel

# ─────────────────────────────────────────────────────────────────────────────
# Injected data-layer contracts (structural — no hard import of app.data)
# ─────────────────────────────────────────────────────────────────────────────


class RecordStore(Protocol):
    """The minimal record access the tools need (a data-layer view)."""

    def get_request(self, request_id: str) -> ServiceRequest | None:
        """Return the request with ``request_id``, or None if absent."""
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

    request_id: str = Field(description="Id of the request to annotate.")
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


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "update_request_status": ToolSpec(
        name="update_request_status",
        description="Change the lifecycle status of a service request.",
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
        description="Assign or unassign a service request to a support agent.",
        args_model=AssignArgs,
        handler=assign_request,
        risk=RiskLevel.MEDIUM,
        # Re-assigning to the same agent converges, and the previous assignee is
        # recoverable from the timeline.
        idempotent=True,
    ),
    "add_case_note": ToolSpec(
        name="add_case_note",
        description="Append (or retract) a note on a service request's timeline.",
        args_model=AddNoteArgs,
        handler=add_case_note,
        risk=RiskLevel.LOW,
        # Re-running appends a second note, so explicitly NOT idempotent.
    ),
}
"""Name → :class:`ToolSpec` for every action tool the domain exposes."""


ALLOWLIST: dict[str, frozenset[str]] = {
    # The operations lead (admin) may perform every action.
    "operations_lead": frozenset(TOOL_REGISTRY),
    # The client (end user) may only annotate their own requests.
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
