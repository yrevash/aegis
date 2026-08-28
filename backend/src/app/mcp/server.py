"""A **real**, multi-tenant MCP server over Streamable HTTP.

This module turns the platform's in-process action tools
(:data:`app.adapter.tools.TOOL_REGISTRY`) plus a small set of **platform** read
tools into first-class **Model Context Protocol** tools an external client
(Claude Desktop, the ``mcp`` CLI, any MCP host) can list and call. It is built
against the installed ``mcp`` SDK (2.x): the transport, the bearer-auth
middleware and the session manager are the SDK's; the governance on top is ours.

Transport and identity (task 10.4)
----------------------------------
The server used to be **stdio-only** with its persona pinned by an environment
variable, which made it single-tenant by construction: one process, one persona,
no caller. Both are gone. :func:`build_http_app` returns the SDK's
**Streamable HTTP** Starlette app, wrapped in the SDK's ``AuthenticationMiddleware``
+ ``RequireAuthMiddleware`` over :class:`AegisTokenVerifier` — the *same* signed
Aegis access token the HTTP API accepts. An unauthenticated request never reaches
a handler; it gets a 401 with a ``WWW-Authenticate`` challenge from the SDK.

Scope is per **call**, not per connection (task 10.5)
-----------------------------------------------------
The failure this is built to avoid is an MCP server that authenticates a
connection once and then trusts every call on it. A Streamable HTTP session is
long-lived and outlives the context that opened it, so:

* :func:`resolve_caller` reads the identity off **this message's own HTTP
  request** (``ServerRequestContext.request``, which the transport attaches per
  inbound POST) — never off a value cached at connection setup, and never off the
  ``auth_context`` contextvar, which in stateful mode belongs to the task group
  that created the session rather than to the call being served.
* The token is re-verified on every call, so an expired bearer stops working
  mid-connection.
* The **authority** is re-read from the ``users`` table on every call, through the
  very same :func:`app.api.routes._login_lookup` the login path uses. A principal
  deactivated, moved to another tenant, or given a different role mid-connection
  does not keep the authority its token was minted under.

Governance is shared, not duplicated
------------------------------------
The resolved principal becomes the same :class:`app.api.routes.AuthContext` the
HTTP API builds, its tenant authority comes from the same sealed
:func:`~aegis.retrieval.types.principal_tenant_scope` (so ``None`` never means
both "platform" and "no tenant"), and its caps come from the same
:func:`app.api.routes._resolve_governance`. The resulting
:class:`~aegis.governance.types.GovernanceContext` is bound for the duration of
one call and unbound in a ``finally`` — which is what makes every audit row an
MCP call writes land under the caller's tenant, because
:func:`aegis.governance.audit.record_audit` reads that context.

Facade, **not** a bypass
------------------------
The server never re-implements a tool or reaches around the platform's controls:

* **Tool list** — :func:`app.adapter.tools.tool_definitions_for` returns the
  *allowlist-filtered* schemas for **the caller's own persona**, derived from the
  caller's RBAC role by :func:`app.api.routes._persona_for`. A tool a persona may
  not call is never listed.
* **Tool call** — routed through :func:`app.adapter.tools.run_tool`, which
  re-checks the allowlist before any side effect and emits an audit row.
* **Platform tools** (:data:`PLATFORM_TOOLS`) are role-gated with the same role
  sets their HTTP siblings use and tenant-scoped through
  :func:`~aegis.retrieval.types.tenant_filter`.

Risk policy (security)
----------------------
* **LOW / MEDIUM** (reads and low-consequence writes) execute through
  :func:`run_tool` and return the real :class:`ToolActionResult`.
* **HIGH** (consequential, externally-visible writes) are **listed** so a client
  can discover them, but a CALL is **not executed**. It enqueues a real
  ``PENDING`` row in the platform's approvals inbox — the same
  :func:`app.data.approvals.enqueue_approval` table the in-run human gate writes,
  with the same ``action``/``args``/``risk`` shape, scoped to the caller's tenant
  and attributed to the caller — and returns the approval id. The protocol does
  not get its own back door: an MCP client is a proposer, not an approver.

Unknown or not-allowlisted tool names are rejected before any execution or audit.

The :mod:`mcp` SDK is imported lazily inside functions/methods so importing
:mod:`app.mcp.server` (and the rest of the app) never requires the SDK.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from aegis.governance.types import GovernanceContext, Role
from aegis.retrieval.types import (
    AllTenants,
    TenantScope,
    UntenantedPrincipalError,
    tenant_filter,
)
from pydantic import BaseModel, Field

from app.adapter import (
    DEFAULT_PERSONA_ID,
    InMemoryRecordStore,
    ToolContext,
    generate_synthetic_sync,
    is_allowed,
    run_tool,
    tool_definitions_for,
)
from app.adapter.tools import (
    TOOL_REGISTRY,
    AuditFn,
    RecordStore,
    ToolNotAllowedError,
    UnknownToolError,
)
from app.api.schemas import RiskLevel
from app.core.security import coarse_role_from_fine, decode_access_token

# ``coarse_role_from_fine`` / ``decode_access_token`` come through the backend shim
# (``app.core.security``) rather than straight from ``aegis.governance.security``:
# importing the shim is what injects this deployment's JWT signing configuration into
# that module. Reaching past it would decode tokens against an unconfigured secret,
# depending on import order.

if TYPE_CHECKING:  # Type-only imports — never require the SDK at import time.
    import mcp.types as mcp_types
    from mcp.server import Server, ServerRequestContext
    from mcp.server.auth.provider import AccessToken
    from starlette.applications import Starlette
    from starlette.types import Receive, Scope, Send

    from app.api.routes import AuthContext

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

SERVER_NAME = "aegis-adapter-tools"
"""MCP server name advertised to clients.

Was ``tcs-adapter-tools``. A platform whose central claim is that it is
**domain-agnostic** — one adapter layer swapped for a new problem — should not hand
every MCP client that connects to it the name of one particular customer. The value is
minted into an ``iss`` claim and never validated against, so the rename carries no
compatibility cost.
"""

SERVER_VERSION = "2.0.0"
"""MCP server version advertised to clients (2.x == Streamable HTTP + RBAC)."""

MCP_ACTOR = "mcp"
"""Actor prefix attributed to every action initiated through this MCP facade.

Over the wire the audit actor is ``mcp:<username>``: the trail has to name *which*
principal proposed an action, not merely that MCP did. The bare prefix remains the
default for the in-process facade, which has no caller.
"""

MCP_RUN_PREFIX = f"{MCP_ACTOR}:"
"""Prefix on the synthetic ``run_id``/``thread_id`` of an MCP-originated approval row.

It is what tells the decide path that this gate is **not** a parked LangGraph run, so
there is no checkpoint to resume and the approved tool has to be executed directly. See
:func:`execute_approved_proposal`.
"""

MCP_PATH = "/mcp"
"""Path the Streamable HTTP transport is served on, inside :data:`MCP_MOUNT`."""

MCP_MOUNT = "/mcp"
"""Where :func:`build_http_app` is mounted on the platform's ASGI app.

The full client URL is therefore ``<origin>/mcp/mcp``. It is a mount rather than a
FastAPI route because the SDK hands us a complete ASGI application (transport,
auth middleware, session manager and its lifespan), and re-hosting that inside a
route would mean re-implementing the parts we deliberately adopted.
"""

MCP_SCOPE = "aegis:mcp"
"""OAuth scope stamped on a verified Aegis token.

Descriptive, **not** the authorization boundary: every valid Aegis principal may
*connect*, and what it may *do* is decided per call by :class:`GovernedMcpServer`
against the live ``users`` row. A connection-level scope check would be exactly the
"authenticate once, trust every call" mistake this server exists to avoid.
"""

CAPABILITIES_URI = "aegis://platform/capabilities"
"""Resource URI for the Aegis capabilities manifest."""

TOOL_POLICY_URI = "aegis://tools/policy"
"""Resource URI for the persona allowlist + risk policy this server enforces."""

# Per-tool MCP annotations come off the tool spec itself — ``ToolSpec.destructive`` and
# ``ToolSpec.idempotent`` — rather than a table here, and they are asserted per tool
# rather than derived from the risk tier, because risk does not imply idempotency: a
# note-append is LOW risk and NOT idempotent, while a gated status transition is HIGH
# risk and IS idempotent. A risk→annotation shortcut would ship a hint that is simply
# untrue, and per the MCP spec these hints are advisory metadata a client may act on.
#
# This module used to hold that table keyed by the shipped domain's three tool names,
# which made it core knowledge of one domain: a retarget renamed every key, every
# lookup missed, and the MCP surface quietly advertised the conservative fallback as
# though it were the domain's own assertion. A tool whose spec declares neither flag
# still falls back to :func:`_conservative_annotations`.


APPROVAL_REQUIRED_MESSAGE = (
    "This HIGH-risk write is a proposal only — it was NOT executed via MCP. It has "
    "been filed as a PENDING row in the platform's approvals inbox, the same "
    "bounded-autonomy gate the console and the in-run agent use, scoped to your "
    "tenant and attributed to you, and a human must decide it there. A high-risk "
    "write is a proposal here, not a decision."
)
"""Human-facing explanation returned when a HIGH-risk tool is called over MCP."""

#: What a principal that resolves to no tenant authority is told. Deliberately the same
#: wording the HTTP API uses, so the refusal does not leak which tenants exist.
UNTENANTED_MESSAGE = (
    "This account is not bound to a tenant, so there is no scope to read. Ask an "
    "administrator to assign it to a tenant."
)


class McpIdentityError(RuntimeError):
    """Raised when a call carries no usable caller identity, or the caller is stale.

    Deliberately raised **per call** rather than per connection: a long-lived session
    whose principal was deactivated, moved between tenants or re-roled must fail on
    the next call, not keep the authority its token was minted under.
    """


class _AuditDefault:
    """Sentinel type meaning "resolve the platform's real audit sink lazily"."""


_USE_DEFAULT_AUDIT = _AuditDefault()
"""Sentinel: resolve the platform's real ``record_audit`` sink lazily."""


def _conservative_annotations(risk: RiskLevel) -> dict[str, bool]:
    """Return safe-by-default MCP hints for a tool with no explicit entry.

    Used only as a fallback for a tool whose spec asserts neither ``destructive`` nor
    ``idempotent``. It assumes the cautious reading — a write, not idempotent — and
    marks HIGH risk as destructive, so a new tool can never be silently advertised to
    a client as safer than it is.

    Args:
        risk: The tool's registry risk tier.

    Returns:
        A mapping of MCP annotation hint names to values.
    """
    return {
        "read_only_hint": False,
        "destructive_hint": risk is RiskLevel.HIGH,
        "idempotent_hint": False,
        "open_world_hint": False,
    }


def _annotations_for(spec: object | None, risk: RiskLevel) -> dict[str, bool]:
    """Return the MCP hints for one tool, read off its own spec.

    Args:
        spec: The registry's tool spec, or ``None`` when the tool is not registered.
        risk: The tool's risk tier (``HIGH`` for an unregistered name).

    Returns:
        A mapping of MCP annotation hint names to values. A spec that asserts neither
        ``destructive`` nor ``idempotent`` gets :func:`_conservative_annotations`, so a
        tool nobody thought about is never advertised as safer than it is.
    """
    destructive = getattr(spec, "destructive", None)
    idempotent = getattr(spec, "idempotent", None)
    if destructive is None and idempotent is None:
        return _conservative_annotations(risk)
    return {
        "read_only_hint": bool(getattr(spec, "read_only", False)),
        "destructive_hint": bool(destructive),
        "idempotent_hint": bool(idempotent),
        "open_world_hint": bool(getattr(spec, "open_world", False)),
    }


def _platform_record_store() -> RecordStore:
    """Return the platform's **one** in-process record store.

    Not a fresh :class:`~app.adapter.InMemoryRecordStore` per MCP server. The facade
    runs the platform's real tools, and running them against a store nothing else can
    see makes the MCP front door a parallel universe: a write tool called over MCP
    answered ``changed: true`` and wrote an audit row, and its effect was invisible to
    the agent graph, to ``/query`` and to the console — because they were all reading a
    different dict. One process, one set of records.

    Falls back to a private store only when the agent package cannot be imported at all,
    which is the same "the domain is not wired here" case the rest of this module treats
    as optional.
    """
    try:
        from app.agent.deps import shared_record_store  # noqa: PLC0415 - avoid a cycle
    except ImportError:  # pragma: no cover - the agent package is always present
        return InMemoryRecordStore.from_dataset(generate_synthetic_sync())
    return shared_record_store()


def _resolve_default_audit() -> AuditFn | None:
    """Return the platform audit sink, or ``None`` if the data layer is absent.

    Returns:
        :func:`app.data.record_audit` when importable; otherwise ``None`` (in
        which case :class:`ToolContext` resolves it lazily at call time, still
        never silently dropping the trail when the sink *is* present).
    """
    try:
        from app.data import record_audit  # noqa: PLC0415
    except ImportError:
        return None
    return record_audit


# ─────────────────────────────────────────────────────────────────────────────
# Platform tools — tenant-scoped reads of Aegis's own governance data
# ─────────────────────────────────────────────────────────────────────────────


class AuditRecentArgs(BaseModel):
    """Arguments for the ``aegis_audit_recent`` platform tool."""

    limit: int = Field(
        default=20, ge=1, le=200, description="How many rows to return (newest first)."
    )


async def _audit_recent(
    auth: AuthContext, scope: TenantScope, args: AuditRecentArgs
) -> dict[str, Any]:
    """Return the caller's own tenant's most recent audit rows.

    The tenant predicate comes from :func:`~aegis.retrieval.types.tenant_filter` over
    the caller's resolved authority — the only sanctioned producer of an unrestricted
    (``None``) filter, and it can only produce one for
    :data:`~aegis.retrieval.types.ALL_TENANTS`. A tenant-bound caller therefore cannot
    reach another tenant's trail even if the row filter were forgotten, because the
    same value is bound as the RLS scope on the reading session.
    """
    from app.data import list_recent_audit  # noqa: PLC0415 - DB is optional at boot

    predicate = tenant_filter(scope)
    rows = await list_recent_audit(limit=args.limit, tenant_id=predicate)
    return {
        "tenant_id": predicate,
        "scope": "all-tenants" if predicate is None else f"tenant:{predicate}",
        "count": len(rows),
        "rows": [row.model_dump(mode="json") for row in rows],
    }


class _PlatformHandler(Protocol):
    """The async signature every platform tool handler implements."""

    async def __call__(
        self, auth: AuthContext, scope: TenantScope, args: Any  # noqa: ANN401
    ) -> dict[str, Any]:
        """Run the tool for ``auth`` within ``scope``.

        ``args`` is the spec's own ``args_model`` instance; the protocol cannot name
        which one without making every handler invariant in its argument model.
        """
        ...


@dataclass(frozen=True)
class PlatformToolSpec:
    """A platform (as opposed to domain-adapter) MCP tool.

    Platform tools read Aegis's *own* governance data rather than the retargetable
    domain's records, so they live here and survive a domain swap. Each one names the
    coarse roles it admits — the same set its HTTP sibling's guard admits — and every
    read is tenant-scoped through the caller's resolved authority.

    Attributes:
        name: The MCP tool name.
        description: Client-visible description.
        args_model: Pydantic model validating (and describing) the arguments.
        roles: The coarse RBAC roles admitted. Mirrors the HTTP guard named in
            :attr:`mirrors`.
        mirrors: The HTTP route whose guard this tool's ``roles`` copies, so the two
            can be compared by a reader (and by a test) instead of drifting quietly.
        risk: The risk tier, applied by the same gate the domain tools go through.
        handler: The async implementation.
    """

    name: str
    description: str
    args_model: type[BaseModel]
    roles: frozenset[Role]
    mirrors: str
    risk: RiskLevel
    handler: _PlatformHandler

    def definition(self) -> dict[str, Any]:
        """Return the MCP/OpenAI ``function`` definition for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


PLATFORM_TOOLS: dict[str, PlatformToolSpec] = {
    "aegis_audit_recent": PlatformToolSpec(
        name="aegis_audit_recent",
        description=(
            "Read the most recent Aegis audit-trail rows visible to you. Scoped to "
            "your own tenant; a platform operator sees every tenant."
        ),
        args_model=AuditRecentArgs,
        # The same pair ``GET /audit`` admits (``require_admin_or_devops``): DevOps
        # legitimately needs the trail, and nobody else may read it.
        roles=frozenset({Role.ADMIN, Role.DEVOPS}),
        mirrors="GET /v1/audit (require_admin_or_devops)",
        risk=RiskLevel.LOW,
        handler=_audit_recent,
    ),
}
"""Name → :class:`PlatformToolSpec` for the platform's own MCP tools."""


# ─────────────────────────────────────────────────────────────────────────────
# Per-call identity — the whole point of task 10.5
# ─────────────────────────────────────────────────────────────────────────────


class AegisTokenVerifier:
    """The SDK's ``TokenVerifier`` protocol, over Aegis's own access tokens.

    Adopted rather than hand-rolled: the SDK's ``BearerAuthBackend`` /
    ``RequireAuthMiddleware`` do the header parsing, the 401 and the
    ``WWW-Authenticate`` challenge; this only says whether a token is one of ours.

    **It deliberately decides nothing about authorization.** The returned
    :class:`~mcp.server.auth.provider.AccessToken` identifies the *connection's*
    principal (which is what the SDK pins a session to, so a session cannot be reused
    with a different credential); every RBAC and tenant decision is made per call
    against the live ``users`` row by :func:`resolve_caller`.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an ``AccessToken`` for a valid Aegis JWT, else ``None``.

        Args:
            token: The raw bearer token.

        Returns:
            The SDK ``AccessToken``, or ``None`` for anything that is not a currently
            valid Aegis access token (bad signature, expired, malformed claims, or a
            fine/coarse role pair that could only come from tampering).
        """
        from mcp.server.auth.provider import AccessToken  # noqa: PLC0415

        try:
            claims = decode_access_token(token)
        except Exception:  # noqa: BLE001 - any decode failure is an auth failure
            return None
        # Defence in depth, identical to ``app.api.routes.require_auth``: every real
        # mint path derives ``coarse_role`` from the fine role, so an inconsistent pair
        # can only be a tampered token.
        if coarse_role_from_fine(claims.role) != claims.coarse_role:
            return None
        return AccessToken(
            token=token,
            client_id=claims.username,
            subject=str(claims.user_id) if claims.user_id is not None else None,
            scopes=[MCP_SCOPE],
            claims={"iss": SERVER_NAME},
        )


class CallerResolver(Protocol):
    """How :class:`GovernedMcpServer` learns who is making the call being served."""

    async def __call__(self, ctx: ServerRequestContext[Any, Any]) -> AuthContext:
        """Resolve the principal for one inbound MCP message."""
        ...


async def resolve_caller(ctx: ServerRequestContext[Any, Any]) -> AuthContext:
    """Resolve the principal for **this one call**, from live state.

    Three deliberate properties, each of which is the mitigation for a way an MCP
    server becomes a hole:

    1. **Per call, not per connection.** The identity is read off ``ctx.request`` —
       the Starlette request the transport attached to *this* inbound message — so a
       call inherits nothing from the request that opened the session. Reading the
       SDK's ``auth_context`` contextvar instead would be wrong as well as stale: in
       stateful mode handlers run in the session manager's task group, not in the
       POST's ASGI task.
    2. **The token is re-verified per call**, so expiry bites mid-connection.
    3. **The authority is re-read from the ``users`` table per call**, through the same
       lookup the login path uses. A token is a claim about who you were when it was
       minted; it is not evidence of what you may do now. Deactivating an account,
       moving a user between tenants or changing their role takes effect on the very
       next call rather than at the next login.

    Args:
        ctx: The SDK's per-request context for the message being served.

    Returns:
        The same :class:`app.api.routes.AuthContext` the HTTP API binds.

    Raises:
        McpIdentityError: When the message carries no HTTP request (i.e. it did not
            arrive over Streamable HTTP), no verified bearer, or when the principal no
            longer exists / is deactivated / cannot be read at all.
    """
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser  # noqa: PLC0415

    request = getattr(ctx, "request", None)
    if request is None:
        raise McpIdentityError(
            "This message carries no HTTP request, so it has no caller identity to "
            "authorise against. Aegis serves MCP over Streamable HTTP only."
        )
    user = request.scope.get("user")
    if not isinstance(user, AuthenticatedUser):
        raise McpIdentityError(
            "This call carries no verified Aegis bearer token. Present one on every "
            "request, not only on the request that opened the session."
        )
    try:
        claims = decode_access_token(user.access_token.token)
    except Exception as exc:  # noqa: BLE001 - any decode failure is an auth failure
        raise McpIdentityError(
            "The bearer token presented on this call is no longer valid."
        ) from exc
    return await live_principal(claims.username)


async def live_principal(username: str) -> AuthContext:
    """Return the **current** principal for ``username``, read from Postgres.

    Uses :func:`app.api.routes._login_lookup` — the same read the login path performs,
    including its ``SECURITY DEFINER`` window under the fail-closed RLS posture — so
    there is one identity source rather than two that can disagree.

    Args:
        username: The username carried by this call's verified token.

    Returns:
        The live :class:`app.api.routes.AuthContext`.

    Raises:
        McpIdentityError: If the account is unknown, deactivated, or the identity store
            cannot be reached. It fails **closed**: falling back to the token's own
            claims is precisely how a revoked principal keeps working.
    """
    from aegis.governance.security import principal_role  # noqa: PLC0415

    from app.api.routes import AuthContext, _login_lookup, _persona_for  # noqa: PLC0415
    from app.data import get_sessionmaker  # noqa: PLC0415 - DB is optional at boot

    try:
        async with get_sessionmaker()() as session:
            row, _provisioned = await _login_lookup(session, username)
    except Exception as exc:  # noqa: BLE001 - unreachable store must not widen access
        raise McpIdentityError(
            "This call cannot be authorised: the identity store could not be reached, "
            "so the caller's current role and tenant are unknown."
        ) from exc
    if row is None or not row.is_active:
        raise McpIdentityError(
            "This account no longer exists or has been deactivated. A token minted "
            "before that change does not carry the authority it used to."
        )
    return AuthContext(
        username=row.username,
        role=row.role,
        persona=_persona_for(row.role),
        fine_role=principal_role(row.role, row.tenant_id),
        tenant_id=row.tenant_id,
        user_id=row.id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The testable facade (one per caller)
# ─────────────────────────────────────────────────────────────────────────────


class AdapterToolServer:
    """Maps the adapter tool registry onto MCP list/call semantics for one persona.

    One of these is built **per authenticated caller** by :class:`GovernedMcpServer`;
    it holds no identity of its own beyond the persona whose allowlist it enforces and
    the optional ``caller`` it attributes gated proposals to. It is also the
    unit-testable core: :meth:`list_tools` and :meth:`call_tool` are the exact
    functions the registered MCP handlers reach, so tests can drive them directly with
    no live client.

    Attributes:
        persona_id: The persona whose allowlist scopes the exposed tools.
        store: The record store tool executions read and write.
        actor: The audit actor attributed to MCP-initiated actions.
        caller: The authenticated principal, when there is one. Only a call with a
            caller can file a gated proposal, because an approvals row with no tenant
            and no requester is an inbox item nobody owns.
    """

    def __init__(
        self,
        persona_id: str | None = None,
        store: RecordStore | None = None,
        *,
        audit: AuditFn | None | _AuditDefault = _USE_DEFAULT_AUDIT,
        actor: str = MCP_ACTOR,
        caller: AuthContext | None = None,
    ) -> None:
        """Configure the facade.

        Args:
            persona_id: Persona whose allowlist to enforce. Defaults to
                :data:`~app.adapter.DEFAULT_PERSONA_ID`. Over the wire this is always
                the caller's own persona — there is no environment override any more;
                ``MCP_PERSONA_ID`` was a process-wide single-tenant pin.
            store: Record store to run tools against. Defaults to an in-memory
                store seeded from :func:`generate_synthetic_sync`.
            audit: Audit sink. Defaults to the platform's ``record_audit`` (so
                the trail is preserved); tests may inject a capturing fake.
            actor: Audit actor id for MCP-initiated actions.
            caller: The authenticated principal this facade acts for, if any.
        """
        self.persona_id = persona_id or DEFAULT_PERSONA_ID
        self.store = (
            store
            if store is not None
            else InMemoryRecordStore.from_dataset(generate_synthetic_sync())
        )
        self._audit: AuditFn | None = (
            _resolve_default_audit() if isinstance(audit, _AuditDefault) else audit
        )
        self.actor = actor
        self.caller = caller

    def _tool_context(self) -> ToolContext:
        """Build the :class:`ToolContext` every tool execution runs inside."""
        return ToolContext(store=self.store, actor=self.actor, audit=self._audit)

    def list_tools(self) -> list[mcp_types.Tool]:
        """Return the allowlist-filtered MCP tool list for the persona.

        Each tool carries the real name, description and JSON-schema emitted by
        :meth:`ToolSpec.definition` — the same definition that drives in-process
        validation — so an MCP client sees exactly what the platform exposes.

        Returns:
            A list of MCP :class:`~mcp.types.Tool` objects.
        """
        tools: list[mcp_types.Tool] = []
        for definition in tool_definitions_for(self.persona_id):
            function = definition["function"]
            name = function["name"]
            spec = TOOL_REGISTRY.get(name)
            risk = spec.risk if spec is not None else RiskLevel.HIGH
            tools.append(
                _tool(
                    name=name,
                    description=function["description"],
                    schema=function["parameters"],
                    risk=risk,
                    hints=_annotations_for(spec, risk),
                )
            )
        return tools

    def list_resources(self) -> list[mcp_types.Resource]:
        """Return the read-only resources this server publishes.

        Resources are the MCP primitive for *context* (application-controlled data)
        as opposed to tools (model-controlled actions). Aegis publishes two, both
        derived from live in-process state rather than a hand-written blurb:

        * :data:`CAPABILITIES_URI` — the honest capabilities manifest, the same
          :data:`app.capabilities.AEGIS_MODULES` source of truth served at
          ``GET /platform/capabilities`` and read by the docs and the console.
        * :data:`TOOL_POLICY_URI` — the persona's allowlist and per-tool risk tiers,
          so a client can discover *before* calling that HIGH-risk writes are gated.

        Returns:
            A list of MCP :class:`~mcp.types.Resource` descriptors.
        """
        import mcp.types as mcp_types  # noqa: PLC0415

        return [
            mcp_types.Resource(
                uri=CAPABILITIES_URI,
                name="aegis-capabilities",
                title="Aegis capabilities manifest",
                description=(
                    "Every Aegis module with its honest underlying technology, "
                    "implementing module path and live/optional status."
                ),
                mimeType="application/json",
            ),
            mcp_types.Resource(
                uri=TOOL_POLICY_URI,
                name="aegis-tool-policy",
                title="Tool allowlist and risk policy",
                description=(
                    f"The tools persona '{self.persona_id}' may call, each with its "
                    "risk tier and whether it is auto-executed or gated behind "
                    "human approval."
                ),
                mimeType="application/json",
            ),
        ]

    def read_resource(self, uri: str) -> str:
        """Return the JSON body of a published resource.

        Args:
            uri: One of :data:`CAPABILITIES_URI` or :data:`TOOL_POLICY_URI`.

        Returns:
            The resource body as a JSON string.

        Raises:
            ValueError: If ``uri`` is not a resource this server publishes.
        """
        if uri == CAPABILITIES_URI:
            from app.capabilities import (  # noqa: PLC0415
                AEGIS_MODULES,
                PRODUCT_NAME,
                PRODUCT_TAGLINE,
            )

            return json.dumps(
                {
                    "product": PRODUCT_NAME,
                    "tagline": PRODUCT_TAGLINE,
                    "module_count": len(AEGIS_MODULES),
                    "modules": [m.model_dump() for m in AEGIS_MODULES],
                },
                indent=2,
            )

        if uri == TOOL_POLICY_URI:
            tools = []
            for definition in tool_definitions_for(self.persona_id):
                name = definition["function"]["name"]
                spec = TOOL_REGISTRY.get(name)
                risk = spec.risk if spec is not None else RiskLevel.HIGH
                tools.append(_policy_row(name, risk))
            return json.dumps(
                {
                    "persona_id": self.persona_id,
                    "actor": self.actor,
                    "policy": (
                        "An MCP client is a proposer, not an approver. LOW and MEDIUM "
                        "risk tools execute through the audited in-process path; HIGH "
                        "risk writes are never auto-executed over MCP."
                    ),
                    "tools": tools,
                },
                indent=2,
            )

        raise ValueError(f"Unknown resource URI: {uri}")

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> mcp_types.CallToolResult:
        """Route an MCP tool call through the platform's controls.

        Order of enforcement mirrors the in-process path: reject unknown /
        not-allowlisted names, then apply the risk gate, then execute the real
        :func:`run_tool` (which re-checks the allowlist and emits audit).

        Args:
            name: The tool name requested by the client.
            arguments: The raw tool arguments (``None`` treated as ``{}``).

        Returns:
            An MCP :class:`~mcp.types.CallToolResult` — the real tool result for
            LOW/MEDIUM tools, an approval-required notice for HIGH-risk tools, or
            an error result for unknown / not-allowlisted / invalid calls.
        """
        arguments = arguments or {}

        # 1) Reject unknown or not-allowlisted tools before any side effect
        #    (the same guards run_tool enforces — surfaced as a clean result).
        if name not in TOOL_REGISTRY:
            return _error_result(f"Unknown tool {name!r}.")
        if not is_allowed(self.persona_id, name):
            return _error_result(
                f"Persona {self.persona_id!r} is not allowed to call tool {name!r}."
            )

        # 2) Preserve risk semantics: HIGH-risk writes are proposals over MCP —
        #    do NOT execute the side effect; file it at the human approval gate.
        spec = TOOL_REGISTRY[name]
        if spec.risk is RiskLevel.HIGH:
            return await self._propose_high_risk(name, arguments)

        # 3) LOW/MEDIUM: execute through the REAL run_tool (allowlist + audit).
        try:
            result = await run_tool(self.persona_id, name, arguments, self._tool_context())
        except (UnknownToolError, ToolNotAllowedError) as exc:  # defence in depth
            return _error_result(str(exc))
        except Exception as exc:  # noqa: BLE0001 — validation errors → error result
            return _error_result(f"{type(exc).__name__}: {exc}")

        return _ok_result(result.model_dump(mode="json"), is_error=not result.ok)

    # -- the HIGH-risk gate ----------------------------------------------------

    async def _propose_high_risk(
        self, name: str, arguments: dict[str, Any]
    ) -> mcp_types.CallToolResult:
        """File a HIGH-risk MCP call at the human gate; execute nothing.

        The proposal lands in the **same** ``approvals`` table the in-run gate writes
        (:func:`app.data.approvals.enqueue_approval`), with the same ``action`` (the
        tool name), the same ``args`` and ``risk=HIGH``, scoped to the caller's tenant
        and attributed to the caller — so it appears in the platform's approvals inbox
        beside gates raised from the console, and the SLA sweeper treats it identically.
        The synthetic ``run_id`` follows the ``prompt_release`` precedent: it is not a
        LangGraph thread, so no checkpoint or live socket is ever involved.

        Filing the row is only skipped when there is no caller at all — the in-process
        facade used by unit tests — because an inbox row with no tenant and no
        requester is an item nobody owns and nobody is scoped to see. The wire path
        always has a caller: :class:`GovernedMcpServer` refuses the call otherwise.
        """
        approval_id = uuid4().hex
        filed: dict[str, Any] | None = None
        if self.caller is not None:
            filed = await self._file_approval(approval_id, name, arguments)
        await self._audit_high_risk_proposal(name, arguments, approval_id)
        payload: dict[str, Any] = {
            "status": "requires_approval",
            "tool": name,
            "risk": RiskLevel.HIGH.value,
            "executed": False,
            "approval_id": approval_id if filed else None,
            "approval": filed,
            "message": APPROVAL_REQUIRED_MESSAGE,
        }
        # A gated high-risk proposal is a legitimate outcome, not a protocol error,
        # so is_error stays False.
        return _ok_result(payload, is_error=False)

    async def _file_approval(
        self, approval_id: str, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Enqueue the PENDING approvals row, or return ``None`` if it could not be."""
        from app.data.approvals import enqueue_approval  # noqa: PLC0415

        caller = self.caller
        assert caller is not None  # noqa: S101 - guarded by the sole call site
        try:
            scope = caller.tenant_scope()
        except UntenantedPrincipalError:  # pragma: no cover - refused upstream
            return None
        try:
            row = await enqueue_approval(
                approval_id=approval_id,
                run_id=f"{MCP_ACTOR}:{approval_id}",
                thread_id=f"{MCP_ACTOR}:{approval_id}",
                action=name,
                args=dict(arguments),
                risk=RiskLevel.HIGH,
                rationale=(
                    f"HIGH-risk action proposed over MCP by {caller.username!r}. "
                    "Not executed; awaiting a human decision at the bounded-autonomy "
                    "gate."
                ),
                tenant_id=tenant_filter(scope),
                requested_by=caller.user_id,
                persona=self.persona_id,
            )
        except Exception:  # noqa: BLE001 - the refusal stands even if filing failed
            logger.error(  # noqa: TRY400 - traceback carried by exc_info
                "MCP HIGH-risk proposal for %s could not be filed at the approvals "
                "gate; the call was still refused and nothing was executed.",
                name,
                exc_info=True,
            )
            return None
        return {
            "id": row.id,
            "status": row.status,
            "tenant_id": row.tenant_id,
            "sla_deadline": row.sla_deadline,
            "requested_by": row.requested_by,
        }

    async def _audit_high_risk_proposal(
        self, name: str, arguments: dict[str, Any], approval_id: str
    ) -> None:
        """Record a HIGH-risk MCP proposal in the audit log (best-effort).

        A probe of a HIGH-risk tool over MCP is never executed, but it must not be
        silent — an auditable trail is exactly the enterprise property the platform
        claims. A sink failure never breaks the (already side-effect-free) response.
        """
        if self._audit is None:
            return
        try:
            await self._audit(
                action=f"mcp.high_risk_proposal:{name}",
                actor=self.actor,
                model=None,
                trace_id=None,
                payload={"tool": name, "arguments": arguments, "executed": False,
                         "risk": RiskLevel.HIGH.value, "approval_id": approval_id},
            )
        except Exception:  # noqa: BLE001 - audit is best-effort, never gates the result
            logger.warning("MCP high-risk-proposal audit failed for %s", name, exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Result / descriptor builders (module-level: the governed layer needs them too)
# ─────────────────────────────────────────────────────────────────────────────


def _tool(
    *, name: str, description: str, schema: dict[str, Any], risk: RiskLevel,
    hints: dict[str, bool],
) -> mcp_types.Tool:
    """Build one MCP tool descriptor with its risk surfaced in the title."""
    import mcp.types as mcp_types  # noqa: PLC0415

    return mcp_types.Tool(
        name=name,
        description=description,
        inputSchema=schema,
        annotations=mcp_types.ToolAnnotations(
            # Surface the platform's own risk tier in the client-visible title: an MCP
            # client (and its user) should be able to see that a call is gated before
            # making it, not after.
            title=f"{name} ({risk.value} risk)",
            **hints,
        ),
    )


def _policy_row(name: str, risk: RiskLevel) -> dict[str, Any]:
    """Describe one tool's disposition for the ``aegis://tools/policy`` resource."""
    gated = risk is RiskLevel.HIGH
    return {
        "name": name,
        "risk": risk.value,
        "auto_executed": not gated,
        "disposition": (
            "proposal only — routed to the human approval gate"
            if gated
            else "executed through the platform's audited tool path"
        ),
    }


def _ok_result(payload: dict[str, Any], *, is_error: bool) -> mcp_types.CallToolResult:
    """Wrap a JSON payload as a text + structured MCP result."""
    import mcp.types as mcp_types  # noqa: PLC0415

    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=json.dumps(payload))],
        structured_content=payload,
        is_error=is_error,
    )


def _error_result(message: str) -> mcp_types.CallToolResult:
    """Build an ``is_error`` MCP result carrying ``message``."""
    import mcp.types as mcp_types  # noqa: PLC0415

    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=message)],
        is_error=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The governed layer — RBAC + tenant scope, resolved per call
# ─────────────────────────────────────────────────────────────────────────────


class GovernedMcpServer:
    """Wraps the facade with per-call identity, RBAC and tenant scope.

    Every method takes the SDK's per-message context and resolves the caller from it
    (:func:`resolve_caller`) **before** doing anything else. Nothing is cached between
    calls: no persona, no tenant, no principal. That is the whole design — a
    Streamable HTTP session is long-lived, and the authority that opened it is not
    evidence about the call being served now.
    """

    def __init__(
        self,
        *,
        store: RecordStore | None = None,
        audit: AuditFn | None | _AuditDefault = _USE_DEFAULT_AUDIT,
        resolve: CallerResolver = resolve_caller,
    ) -> None:
        """Configure the governed layer.

        Args:
            store: The shared record store domain tools act on. Built once (not per
                call) so a write by one call is visible to the next.
            audit: Audit sink; defaults to the platform's ``record_audit``.
            resolve: The per-call identity resolver. Injectable so a test can drive
                the governed path without an HTTP request, and for no other reason —
                the wire path always uses :func:`resolve_caller`.
        """
        self._store = store if store is not None else _platform_record_store()
        self._audit = audit
        self._resolve = resolve

    def facade_for(self, auth: AuthContext | None = None) -> AdapterToolServer:
        """Build the persona-scoped facade for one caller (or the default persona).

        Args:
            auth: The authenticated caller. ``None`` yields the default-persona facade
                used for inspection and by the offline unit tests; the wire path always
                passes a caller.

        Returns:
            An :class:`AdapterToolServer` sharing this server's store and audit sink.
        """
        if auth is None:
            return AdapterToolServer(store=self._store, audit=self._audit)
        return AdapterToolServer(
            persona_id=auth.persona,
            store=self._store,
            audit=self._audit,
            actor=f"{MCP_ACTOR}:{auth.username}",
            caller=auth,
        )

    async def _caller(
        self, ctx: ServerRequestContext[Any, Any]
    ) -> tuple[AuthContext, TenantScope]:
        """Resolve this call's principal and its tenant authority.

        Raises:
            McpIdentityError: When there is no usable caller, or when the caller
                resolves to no tenant authority at all. The two are one class on
                purpose: both mean "this call cannot be authorised", and neither may
                fall through into an unscoped read.
        """
        auth: AuthContext = await self._resolve(ctx)
        try:
            return auth, auth.tenant_scope()
        except UntenantedPrincipalError as exc:
            raise McpIdentityError(UNTENANTED_MESSAGE) from exc

    async def list_tools(
        self, ctx: ServerRequestContext[Any, Any]
    ) -> list[mcp_types.Tool]:
        """Return the tools **this caller** may call, domain and platform alike.

        Raises:
            McpIdentityError: When the call carries no usable identity.
        """
        auth, _scope = await self._caller(ctx)
        tools = self.facade_for(auth).list_tools()
        tools.extend(
            _tool(
                name=spec.name,
                description=spec.description,
                schema=spec.args_model.model_json_schema(),
                risk=spec.risk,
                hints={
                    "read_only_hint": True,
                    "destructive_hint": False,
                    "idempotent_hint": True,
                    "open_world_hint": False,
                },
            )
            for spec in PLATFORM_TOOLS.values()
            if auth.role in spec.roles
        )
        return tools

    async def call_tool(
        self,
        ctx: ServerRequestContext[Any, Any],
        name: str,
        arguments: dict[str, Any] | None,
    ) -> mcp_types.CallToolResult:
        """Authorise and run one tool call under the caller's own governance context.

        The :class:`~aegis.governance.types.GovernanceContext` bound here is the one
        :func:`app.api.routes._resolve_governance` builds for the HTTP API — the same
        function, not a copy — and it is bound for this call only and unbound in a
        ``finally``. Binding it is what attributes the audit rows the tool writes to
        the caller's tenant, because :func:`aegis.governance.audit.record_audit` reads
        the bound context when no explicit tenant is passed.
        """
        from app.api.routes import _resolve_governance  # noqa: PLC0415
        from app.core.governance import (  # noqa: PLC0415
            reset_governance_context,
            set_governance_context,
        )

        try:
            auth, scope = await self._caller(ctx)
        except McpIdentityError as exc:
            return _error_result(str(exc))

        governance = await _resolve_governance(auth)
        token = set_governance_context(governance)
        try:
            if name in PLATFORM_TOOLS:
                return await self._call_platform_tool(auth, scope, name, arguments or {})
            return await self.facade_for(auth).call_tool(name, arguments)
        finally:
            reset_governance_context(token)

    async def _call_platform_tool(
        self,
        auth: AuthContext,
        scope: TenantScope,
        name: str,
        arguments: dict[str, Any],
    ) -> mcp_types.CallToolResult:
        """Run one platform tool after its RBAC check, scoped to ``scope``."""
        spec = PLATFORM_TOOLS[name]
        if auth.role not in spec.roles:
            admitted = ", ".join(sorted(r.value for r in spec.roles))
            return _error_result(
                f"Tool {name!r} requires one of the roles: {admitted}. This account "
                f"holds {auth.role.value!r}."
            )
        try:
            args = spec.args_model.model_validate(arguments)
        except Exception as exc:  # noqa: BLE001 - validation errors → error result
            return _error_result(f"{type(exc).__name__}: {exc}")
        try:
            payload = await spec.handler(auth, scope, args)
        except Exception as exc:  # noqa: BLE001 - never leak a traceback to a client
            logger.warning("MCP platform tool %s failed", name, exc_info=True)
            return _error_result(f"{type(exc).__name__}: {exc}")
        return _ok_result(payload, is_error=False)

    async def list_resources(
        self, ctx: ServerRequestContext[Any, Any]
    ) -> list[mcp_types.Resource]:
        """Return the resources this caller may read."""
        auth, _scope = await self._caller(ctx)
        return self.facade_for(auth).list_resources()

    async def read_resource(
        self, ctx: ServerRequestContext[Any, Any], uri: str
    ) -> str:
        """Return one resource body, computed for this caller's persona and role.

        The tool-policy resource is the caller's *own* policy: it names the persona
        their role adopts, the domain tools that persona may call, and the platform
        tools their role admits. A client can therefore discover what is gated for
        **them** before calling anything.
        """
        auth, scope = await self._caller(ctx)
        body = self.facade_for(auth).read_resource(uri)
        if uri != TOOL_POLICY_URI:
            return body
        document = json.loads(body)
        document["role"] = auth.role.value
        document["tenant_scope"] = (
            "all-tenants" if isinstance(scope, AllTenants) else f"tenant:{scope}"
        )
        document["tools"].extend(
            _policy_row(spec.name, spec.risk)
            for spec in PLATFORM_TOOLS.values()
            if auth.role in spec.roles
        )
        return json.dumps(document, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# The other half of the gate — carrying out what a human approved
# ─────────────────────────────────────────────────────────────────────────────


def is_mcp_proposal(run_id: str | None) -> bool:
    """Return whether ``run_id`` belongs to an MCP-originated approvals row."""
    return bool(run_id) and str(run_id).startswith(MCP_RUN_PREFIX)


async def execute_approved_proposal(
    approval_id: str, *, approver: str | None = None
) -> bool:
    """Run the HIGH-risk tool an MCP caller proposed, now that a human has approved it.

    **Why this exists.** A gate that can be approved and can never complete is not a
    gate; it is a queue item that dead-ends. An MCP proposal is not a parked LangGraph
    run, so :func:`app.agent.resume_parked_run` finds no checkpoint and reports "nothing
    to resume" — which left the durable row wedged in ``RESUMING`` for ever: invisible to
    the pending inbox, unmatched by :func:`app.data.resolve_approval` and by the SLA
    sweeper, and rendered by the console as an action that is *in flight*. The operator
    was told ``accepted: true`` about something that had not happened and never would.

    So the approval is carried out here, under the authority the row itself records:

    * the **persona** stored on the row, whose allowlist is re-checked — a grant may have
      been withdrawn between the proposal and the decision, and the decision does not
      re-authorise a tool the persona has since lost;
    * the **tenant** stored on the row, bound as the governance context, so the execution
      audits under the tenant that proposed it and not under whoever pressed approve;
    * the **approver**, carried into :class:`~app.adapter.ToolContext` so the audit row
      names the human the action rests on.

    Exactly-once is the caller's: :func:`app.agent.decide_approval` has already won the
    optimistic ``PENDING → RESUMING`` transition keyed by ``approval_id``, so only one
    decision ever reaches this function for a given row.

    Args:
        approval_id: The approvals row that was just approved.
        approver: The human who approved it.

    Returns:
        ``True`` when the tool was executed (whatever it returned), ``False`` when this
        row is not an MCP proposal or could not be executed — in which case the caller
        must release the row rather than leave it claiming to be resuming.
    """
    from app.core.governance import (  # noqa: PLC0415 - avoid an import cycle
        reset_governance_context,
        set_governance_context,
    )
    from app.data import get_approval, record_audit  # noqa: PLC0415 - DB optional at boot

    row = await get_approval(approval_id)
    if row is None or not is_mcp_proposal(row.run_id):
        return False

    persona = row.persona or DEFAULT_PERSONA_ID
    name = row.action
    if name not in TOOL_REGISTRY or not is_allowed(persona, name):
        logger.error(
            "Approved MCP proposal %s names tool %r, which persona %r may not call now. "
            "Nothing was executed; the gate is released rather than closed.",
            approval_id,
            name,
            persona,
        )
        return False

    # `Role.ADMIN` here is a KNOWN over-attribution, and it is bounded.
    #
    # What it does NOT do is widen what may run: the persona allowlist is re-checked
    # immediately above, against the persona on the row, and an action the persona may
    # no longer call is refused before this line. Tool authority comes from that check.
    #
    # What it DOES affect is which budget applies, because `GovernanceContext.role`
    # feeds limit resolution — so an approved action resolves against admin limits
    # rather than the requester's, and a tighter per-user cap would not bind it. The
    # tenant is correct (`row.tenant_id`), so the spend lands in the right tenant.
    #
    # The fix needs the requester's real role, and the row carries only `requested_by`
    # (a `users.id`), so it means a lookup on the execution path. Recorded rather than
    # guessed at.
    governance = GovernanceContext(tenant_id=row.tenant_id, role=Role.ADMIN)
    token = set_governance_context(governance)
    try:
        result = await run_tool(
            persona,
            name,
            dict(row.args or {}),
            ToolContext(
                store=_platform_record_store(),
                actor=f"{MCP_ACTOR}:approved",
                approved_by=approver,
                audit=record_audit,
            ),
        )
    except Exception:  # noqa: BLE001 - a failed execution must not close the gate
        logger.error(  # noqa: TRY400 - traceback carried by exc_info
            "Approved MCP proposal %s failed while executing %r; the gate is released "
            "rather than closed.",
            approval_id,
            name,
            exc_info=True,
        )
        return False
    finally:
        reset_governance_context(token)

    logger.info(
        "Approved MCP proposal %s executed %r (ok=%s) for tenant %s, approved by %s.",
        approval_id,
        name,
        result.ok,
        row.tenant_id,
        approver,
    )
    return True


# ─────────────────────────────────────────────────────────────────────────────
# MCP server assembly + the Streamable HTTP app
# ─────────────────────────────────────────────────────────────────────────────


def build_server(
    store: RecordStore | None = None,
    *,
    audit: AuditFn | None | _AuditDefault = _USE_DEFAULT_AUDIT,
    resolve: CallerResolver = resolve_caller,
) -> Server:
    """Build a real :class:`mcp.server.Server` fronting the tool registry.

    Every handler resolves the caller from its own per-message context before doing
    anything, so there is no server-wide persona to configure any more — the
    ``persona_id`` parameter this function used to take was the single-tenant pin.

    Args:
        store: Record store to run tools against (shared across callers).
        audit: Audit sink (see the facade).
        resolve: Per-call identity resolver; injectable for tests only.

    Returns:
        A configured low-level MCP :class:`~mcp.server.Server`. It carries
        ``server.governed`` (the governed layer) and ``server.facade`` (the
        default-persona facade, for inspection and offline tests — the wire path never
        uses it).
    """
    import mcp.types as mcp_types  # noqa: PLC0415
    from mcp.server import Server  # noqa: PLC0415
    from mcp.shared.exceptions import MCPError  # noqa: PLC0415

    governed = GovernedMcpServer(store=store, audit=audit, resolve=resolve)

    def _refuse(exc: McpIdentityError) -> MCPError:
        """Turn a per-call identity refusal into a protocol-level error."""
        # ``mcp.types``, not the separately installed top-level ``mcp_types`` package
        # this line used to import from. That package is a transitive dependency the
        # SDK happens to pull in and re-export, so nothing declares it: the day it
        # stops being pulled in, ``build_server`` — the whole MCP front door —
        # ImportErrors at first use, and ``app.main`` logs "the mcp SDK is not
        # installed" about an SDK that is.
        return MCPError(code=mcp_types.INVALID_REQUEST, message=str(exc))

    async def on_list_tools(
        ctx: ServerRequestContext[Any, Any],
        _params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        try:
            return mcp_types.ListToolsResult(tools=await governed.list_tools(ctx))
        except McpIdentityError as exc:
            raise _refuse(exc) from exc

    async def on_call_tool(
        ctx: ServerRequestContext[Any, Any],
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        return await governed.call_tool(ctx, params.name, params.arguments)

    async def on_list_resources(
        ctx: ServerRequestContext[Any, Any],
        _params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListResourcesResult:
        try:
            return mcp_types.ListResourcesResult(
                resources=await governed.list_resources(ctx)
            )
        except McpIdentityError as exc:
            raise _refuse(exc) from exc

    async def on_read_resource(
        ctx: ServerRequestContext[Any, Any],
        params: mcp_types.ReadResourceRequestParams,
    ) -> mcp_types.ReadResourceResult:
        try:
            text = await governed.read_resource(ctx, str(params.uri))
        except McpIdentityError as exc:
            raise _refuse(exc) from exc
        return mcp_types.ReadResourceResult(
            contents=[
                mcp_types.TextResourceContents(
                    uri=params.uri, mimeType="application/json", text=text
                )
            ]
        )

    server: Server = Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=(
            "Adapter action tools for the service-request domain, plus Aegis platform "
            "reads. Every call is authorised against the caller's live role and "
            "tenant, so the tools you see are the tools you may call. HIGH-risk "
            "writes are filed at the human approval gate and are never executed over "
            "MCP. Read aegis://tools/policy for your own risk policy, and "
            "aegis://platform/capabilities for the module manifest."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_read_resource=on_read_resource,
    )
    server.governed = governed  # type: ignore[attr-defined]
    server.facade = governed.facade_for()  # type: ignore[attr-defined]
    return server


def build_http_app(
    store: RecordStore | None = None,
    *,
    audit: AuditFn | None | _AuditDefault = _USE_DEFAULT_AUDIT,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    json_response: bool = False,
) -> Starlette:
    """Return the **Streamable HTTP** ASGI app for the MCP server.

    Everything about the transport is the SDK's: ``StreamableHTTPSessionManager``,
    the bearer-auth backend, ``RequireAuthMiddleware`` (which answers 401 with a
    ``WWW-Authenticate`` challenge before a handler ever runs), the session-owner
    check that refuses to let one session be reused with a different credential, and
    the DNS-rebinding guard. Aegis supplies the token verifier and the governance.

    The returned app owns a lifespan (``session_manager.run()``); mount it on an ASGI
    host that runs it — see :func:`app.main.create_app`.

    Args:
        store: Record store the domain tools act on.
        audit: Audit sink.
        allowed_hosts: ``Host`` header values the transport accepts. Defaults to the
            configured :attr:`~app.config.Settings.mcp_allowed_hosts`.
        allowed_origins: ``Origin`` header values accepted (browser clients).
        json_response: Answer with a single JSON body instead of an SSE stream.

    Returns:
        The Starlette app serving MCP at :data:`MCP_PATH`.
    """
    from mcp.server.auth.settings import AuthSettings  # noqa: PLC0415
    from mcp.server.transport_security import TransportSecuritySettings  # noqa: PLC0415

    from app.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    hosts = allowed_hosts if allowed_hosts is not None else settings.mcp_allowed_host_list
    origins = (
        allowed_origins
        if allowed_origins is not None
        else [o for h in hosts for o in (f"http://{h}", f"https://{h}")]
    )
    server = build_server(store=store, audit=audit)
    return server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=json_response,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=origins,
        ),
        auth=AuthSettings(
            # Aegis mints its own tokens at ``POST /v1/auth/login``; there is no
            # separate authorization server, so the issuer and the resource are the
            # same deployment. ``required_scopes`` is deliberately empty — see
            # :data:`MCP_SCOPE`.
            issuer_url=settings.mcp_issuer_url,
            resource_server_url=settings.mcp_issuer_url,
            required_scopes=[],
        ),
        token_verifier=AegisTokenVerifier(),
    )


class McpTransportMount:
    """The restartable ASGI object mounted at :data:`MCP_MOUNT`.

    A ``StreamableHTTPSessionManager``'s ``run()`` context is **single-use** — the SDK
    says so in as many words — and :func:`build_http_app` builds one per app. Mounting
    that app for the life of the process therefore makes the *second* entry of the
    host's lifespan raise, which is not a hypothetical: the suite enters
    ``app.main.lifespan`` several times, and any host that stops and restarts does the
    same. So the mount holds a **slot**, and :meth:`running` fills it with a freshly
    built transport for the duration of one lifespan and empties it afterwards.

    A request that arrives with the slot empty gets a 503 naming the cause, rather than
    the SDK's ``RuntimeError: Task group is not initialized`` surfacing as a 500.

    The record store is built once and handed to every rebuild, so a restart of the
    transport does not silently discard the domain state the previous one wrote — and
    it is the platform's **one** store (:func:`app.agent.deps.shared_record_store`),
    not a second one minted here. Minting one made the MCP front door a parallel
    universe: a write tool called over MCP returned ``changed: true``, wrote an audit
    row, and its effect was invisible to the agent, the console and every HTTP read,
    because they were all looking at a different dict.
    """

    def __init__(self, store: RecordStore | None = None) -> None:
        """Create the mount. Nothing is started until :meth:`running` is entered."""
        self._store = store
        self.app: Starlette | None = None

    @asynccontextmanager
    async def running(self) -> AsyncIterator[Starlette]:
        """Build and run the transport for the duration of one host lifespan."""
        inner = build_http_app(self._store)
        async with inner.router.lifespan_context(inner):
            self.app = inner
            try:
                yield inner
            finally:
                self.app = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Delegate to the running transport, or refuse with a 503."""
        inner = self.app
        if inner is None:
            from starlette.responses import JSONResponse  # noqa: PLC0415

            response = JSONResponse(
                {
                    "error": "mcp_transport_not_running",
                    "error_description": (
                        "The MCP transport is not running in this process. It is "
                        "started by the application lifespan; a host that does not "
                        "run one serves no MCP."
                    ),
                },
                status_code=503,
            )
            await response(scope, receive, send)
            return
        await inner(scope, receive, send)
