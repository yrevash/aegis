"""The MCP control plane — connections, control, and the tier each tool is gated at.

Tasks 10.6 and 10.7. This is the **operator surface for MCP**: where a platform admin
declares which external Model Context Protocol servers Aegis may reach, proves one is
actually reachable, turns it off without losing it, and decides — per named tool — what
risk tier a call gates at.

The six routes, and why each is separate
----------------------------------------
``GET /mcp/console``
    One aggregate: declared peers, every tool discovered on them, the grant over each,
    the risk tier each gates at, **Aegis's own tools read-only**, the persona ids a
    grant may name, the recent tier decisions, and the URL of this deployment's own MCP
    endpoint. One call rather than six, for the reason ``GET /governance/dashboard`` is
    one call: the page renders a single consistent picture, and six round trips can
    disagree with each other halfway down it.
``POST /mcp/servers``
    Declare a peer: id (which becomes the tool namespace), label, URL, the header its
    credential goes in, and the credential itself. Declaring grants nothing.
``PUT /mcp/servers/{server_id}``
    Edit it, including ``enabled``. Disabling is not a soft revocation — a disabled
    peer's tools leave the planner's payload — and it is deliberately not deletion, so
    an operator can stop a misbehaving peer without losing how it was configured.
``DELETE /mcp/servers/{server_id}``
    Forget it, with its tools, its grants and its credential.
``POST /mcp/servers/{server_id}/test``
    Connect, report what the peer said — its name, the protocol version it negotiated,
    the tools it advertises — and record the tools it will admit. A saved row proves
    somebody typed a URL; this proves the URL answers, which is the failure mode worth
    guarding against.
``PUT /mcp/tools/{tool_name}/grant``
    Admit a tool for a set of personas at a risk tier. **This is the only write in the
    product that can move an action out of the human gate**, and it is audited with the
    tier before, the tier after, the actor and the reason.

Who may touch any of it (7.16)
------------------------------
``require_platform_admin``, never ``require_admin``: the latter admits the tenant-admin
tier, and a tenant lowering the gate on a tool it does not own is the shape row 15
forbids. It sits beside the database console (:mod:`app.api.routes_db`) for the same
reason and behind the same tier.

Credentials
-----------
Write-only, by construction rather than by discipline. A credential posted here is
handed to :meth:`~app.mcp.client.ExternalToolRegistry.set_credential`, which holds it in
the serving process; the durable row keeps only a twelve-character fingerprint, who set
it and when. There is no accessor that returns it, no field on any response model that
could carry it, and it is never logged — the same treatment ``GENAILAB_API_KEY`` gets,
which is also the way a deployment can supply one without a human
(``AEGIS_MCP_CRED_<SERVER_ID>``).

What this plane deliberately does not do
----------------------------------------
It never executes an external tool. A call happens through the agent's ``act`` node —
after the gate — or not at all; an "execute" button here would be exactly the side door
around seven phases of governance that §10.6 exists to close. The console can *see* the
tools and *decide* their tier; running one is the agent's path, with the human gate in
it. Nor can it touch an Aegis tool's tier: those come from ``ToolSpec.risk`` in
:mod:`app.adapter.tools`, which is domain knowledge versioned with the code, and the
retargeting seam exists precisely so that does not become runtime state. They are
listed, read-only, with the module that declares them.

**Aegis's own MCP endpoint** (10.7) is reported, not proxied. The admin console speaks
Streamable HTTP to it directly with the official TypeScript SDK, so the browser is a
real MCP client of the same governed server Claude Desktop would connect to — not a
bespoke fetch wrapper drifting from the spec. This route only says *where it is*, and
says so honestly: when the deployment has not configured one, the response carries
``null`` and the console renders a stated absence rather than a broken button.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.adapter.tools import ALLOWLIST, TOOL_REGISTRY, UnknownToolError
from app.api.routes import AuthContext, require_platform_admin
from app.api.schemas import RiskLevel
from app.config import get_settings
from app.mcp.client import (
    ExternalServerSpec,
    ExternalToolCollisionError,
    UnknownExternalServerError,
    credential_fingerprint,
    get_registry,
    is_external_name,
)

logger = logging.getLogger(__name__)

mcp_router = APIRouter()

__all__ = ["MCPConsole", "load_servers", "mcp_router", "mount"]

#: The audit action a tier decision is written under. One string, used by the writer and
#: by the reader below, so the trail cannot be recorded under a name nothing looks for.
RISK_AUDIT_ACTION = "mcp.tool_risk_decided"

#: The audit action a connection change is written under.
SERVER_AUDIT_ACTION = "mcp.server_changed"

#: How many recent tier decisions the console carries. A page, not a log viewer — the
#: full history is in the audit surface, which is where a real investigation goes.
AUDIT_TAIL = 25


# ─────────────────────────────────────────────────────────────────────────────
# Wire schemas
# ─────────────────────────────────────────────────────────────────────────────


class MCPServerRow(BaseModel):
    """One declared external MCP server, as the console shows it.

    There is deliberately no credential field. Not "omitted from the response" — the
    model has no place to put one, so no future edit can start returning it by
    accident.
    """

    model_config = ConfigDict(frozen=True)

    server_id: str = Field(
        serialization_alias="serverId",
        description="The Aegis-side id, which is the tool namespace.",
    )
    label: str = Field(description="Human-facing name.")
    url: str = Field(description="The peer's Streamable HTTP endpoint ('' if in-process).")
    auth_header: str = Field(
        serialization_alias="authHeader",
        description="The header this peer's credential is sent in.",
    )
    enabled: bool = Field(
        description="False means its tools leave the agent's payload entirely."
    )
    has_credential: bool = Field(
        serialization_alias="hasCredential",
        description="Whether a credential is available to this process for this peer.",
    )
    credential_fingerprint: str = Field(
        serialization_alias="credentialFingerprint",
        description="Twelve hex characters of SHA-256, or ''. Never the credential.",
    )
    credential_set_by: str | None = Field(
        default=None,
        serialization_alias="credentialSetBy",
        description="Who last set the credential, when it was set through the console.",
    )
    discovered_tools: int = Field(
        serialization_alias="discoveredTools",
        description="Tools currently discovered on this peer.",
    )
    granted_tools: int = Field(
        serialization_alias="grantedTools",
        description="Of those, how many any persona may call.",
    )


class MCPToolRow(BaseModel):
    """One external tool, its tier, and who may call it."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="The qualified Aegis-side name, e.g. mcp__acme__search.")
    server_id: str = Field(
        serialization_alias="serverId", description="The peer it lives on."
    )
    remote_name: str = Field(
        serialization_alias="remoteName",
        description="The name the peer knows it by (what goes on the wire).",
    )
    description: str = Field(
        description="The peer's description, after the TOOL_RESULT rail screened it."
    )
    risk: RiskLevel = Field(
        description="The tier this call gates at. HIGH unless a platform admin lowered it."
    )
    risk_is_default: bool = Field(
        serialization_alias="riskIsDefault",
        description="True when the tier is the untouched HIGH default, not a decision.",
    )
    personas: list[str] = Field(description="Persona ids admitted. Empty means nobody.")
    reason: str = Field(description="Why the tier was set as it is; '' when never set.")
    callable_now: bool = Field(
        serialization_alias="callableNow",
        description="False when the owning server is disabled — the tool is not offered.",
    )


class AegisToolRow(BaseModel):
    """One of Aegis's own tools — shown, never editable here."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    risk: RiskLevel = Field(description="Declared on the ToolSpec, in code.")
    personas: list[str] = Field(description="Persona ids the adapter allowlist admits.")
    declared_in: str = Field(
        serialization_alias="declaredIn",
        description="The module the tier is declared in, so the reader can go and check.",
    )


class MCPDecision(BaseModel):
    """One recorded tier decision — the before, the after, the actor and the reason."""

    model_config = ConfigDict(frozen=True)

    at: str = Field(description="ISO 8601 UTC timestamp.")
    actor: str = Field(description="Who decided.")
    tool: str = Field(description="The tool whose tier was decided.")
    risk_before: str = Field(serialization_alias="riskBefore")
    risk_after: str = Field(serialization_alias="riskAfter")
    personas_before: list[str] = Field(serialization_alias="personasBefore")
    personas_after: list[str] = Field(serialization_alias="personasAfter")
    reason: str


class MCPProbe(BaseModel):
    """What a peer answered when the console tested the connection."""

    model_config = ConfigDict(frozen=True)

    server_id: str = Field(serialization_alias="serverId")
    reachable: bool
    server_name: str = Field(serialization_alias="serverName")
    protocol_version: str = Field(serialization_alias="protocolVersion")
    tools: list[str] = Field(description="Remote tool names, in the peer's own namespace.")
    detail: str = Field(description="The peer's own failure sentence, or ''.")


class MCPConsole(BaseModel):
    """Everything the admin MCP console renders, in one response."""

    model_config = ConfigDict(frozen=True)

    servers: list[MCPServerRow]
    tools: list[MCPToolRow]
    aegis_tools: list[AegisToolRow] = Field(serialization_alias="aegisTools")
    decisions: list[MCPDecision]
    personas: list[str] = Field(
        description="The persona ids a grant may name — read from the adapter allowlist."
    )
    gate_risk: RiskLevel = Field(
        serialization_alias="gateRisk",
        description="The tier at or above which a call stops at the human gate.",
    )
    self_endpoint: str | None = Field(
        serialization_alias="selfEndpoint",
        description=(
            "Aegis's own MCP endpoint for the console's client, or null when this "
            "deployment has not configured one."
        ),
    )
    probe: MCPProbe | None = Field(
        default=None, description="Set only on a test-connection response."
    )


class ServerCreate(BaseModel):
    """A new external MCP connection."""

    model_config = ConfigDict(extra="forbid")

    server_id: str = Field(
        validation_alias="serverId",
        description="Lowercase letters, digits and hyphens. Becomes the tool namespace.",
    )
    label: str = Field(default="", description="Human-facing name.")
    url: str = Field(default="", description="The peer's Streamable HTTP endpoint.")
    auth_header: str = Field(
        default="Authorization",
        validation_alias="authHeader",
        description="The header the credential is sent in.",
    )
    credential: str = Field(
        default="",
        description=(
            "The peer's secret. Held in the serving process only — never written to "
            "Aegis's database and never returned by any route."
        ),
    )
    enabled: bool = Field(default=True)


class ServerUpdate(BaseModel):
    """An edit to an existing connection. Every field is optional; null leaves it alone."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    url: str | None = None
    auth_header: str | None = Field(default=None, validation_alias="authHeader")
    enabled: bool | None = None
    credential: str | None = Field(
        default=None,
        description="A new secret, or '' to forget the one this process holds.",
    )


class GrantWrite(BaseModel):
    """A platform admin's decision about one external tool.

    ``extra="forbid"``: a body carrying a field this model does not know is a 422, not
    a silent drop. That is the rule the four §8.8 incidents were caused by breaking,
    and it matters most on the one write in the product that can move an action out of
    the human gate — a misspelled ``risk`` must not answer 200 and leave HIGH in place
    while the operator believes they lowered it.
    """

    model_config = ConfigDict(extra="forbid")

    personas: list[str] = Field(
        default_factory=list,
        description="Persona ids admitted. An empty list revokes the grant.",
    )
    risk: RiskLevel = Field(
        default=RiskLevel.HIGH,
        description="The tier the call gates at. Lowering it is a deliberate decision.",
    )
    reason: str = Field(
        default="", description="Why — shown in the console beside the tier."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Durable connections
# ─────────────────────────────────────────────────────────────────────────────


async def load_servers() -> int:
    """Hydrate the process registry from ``mcp_servers``. Returns how many were loaded.

    Called once from the app lifespan, after the database is up. Connections an
    operator added through the console are the durable half of this plane; without this
    they would exist only until the process that created them stopped, which is not a
    connection an operator can rely on.

    A row whose id is already declared (from ``AEGIS_MCP_CLIENT_SERVERS``) is
    **updated** rather than skipped or duplicated: the database is the operator's
    statement and configuration is the deployment's, and the operator edited more
    recently by construction — they had to be running against this deployment to write
    the row at all.
    """
    from app.data import McpServer, get_sessionmaker  # noqa: PLC0415

    registry = get_registry()
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(McpServer))).scalars().all()
    for row in rows:
        spec = ExternalServerSpec(
            server_id=row.id,
            url=row.url,
            label=row.label,
            auth_header=row.auth_header,
            enabled=row.enabled,
        )
        try:
            registry.register_server(spec)
        except ValueError:
            registry.update_server(
                row.id,
                label=row.label,
                url=row.url,
                auth_header=row.auth_header,
                enabled=row.enabled,
            )
    return len(rows)


async def _persist(spec: ExternalServerSpec, *, actor: str, secret: str | None) -> None:
    """Insert or update the durable row for ``spec``.

    ``secret`` is used only to compute a fingerprint and is not stored. The parameter
    exists so the fingerprint and the in-process credential cannot drift apart: they are
    set in the same call or not at all.
    """
    from app.data import McpServer, get_sessionmaker  # noqa: PLC0415

    async with get_sessionmaker()() as session:
        row = await session.get(McpServer, spec.server_id)
        now = datetime.now(UTC)
        if row is None:
            row = McpServer(id=spec.server_id, created_by=actor)
            session.add(row)
        row.label = spec.label
        row.url = spec.url
        row.auth_header = spec.auth_header
        row.enabled = spec.enabled
        row.updated_at = now
        if secret is not None:
            row.credential_fingerprint = credential_fingerprint(secret) if secret else ""
            row.credential_set_by = actor if secret else None
            row.credential_set_at = now if secret else None
        await session.commit()


async def _forget(server_id: str) -> None:
    """Delete the durable row for ``server_id`` (a no-op when there is none)."""
    from app.data import McpServer, get_sessionmaker  # noqa: PLC0415

    async with get_sessionmaker()() as session:
        row = await session.get(McpServer, server_id)
        if row is not None:
            await session.delete(row)
            await session.commit()


async def _stored_credential_meta() -> dict[str, tuple[str, str | None]]:
    """Return ``server_id → (fingerprint, set_by)`` from the durable rows."""
    from app.data import McpServer, get_sessionmaker  # noqa: PLC0415

    try:
        async with get_sessionmaker()() as session:
            rows = (await session.execute(select(McpServer))).scalars().all()
    except Exception:  # noqa: BLE001 - the console renders without the database
        logger.warning("MCP console: could not read mcp_servers", exc_info=True)
        return {}
    return {row.id: (row.credential_fingerprint, row.credential_set_by) for row in rows}


# ─────────────────────────────────────────────────────────────────────────────
# Audit
# ─────────────────────────────────────────────────────────────────────────────


async def _audit(action: str, actor: str, payload: dict[str, Any]) -> None:
    """Write one control-plane audit row, best-effort.

    Best-effort because an audit sink that can refuse a governance write turns a
    degraded database into a frozen console — but never silent: a failure is logged at
    WARNING with the payload's own key facts.
    """
    try:
        from app.data import record_audit  # noqa: PLC0415

        await record_audit(
            action=action, actor=actor, model=None, trace_id=None, payload=payload
        )
    except Exception:  # noqa: BLE001 - never let the trail block the decision
        logger.warning("MCP console: audit write failed for %s %s", action, payload)


async def _decisions() -> list[MCPDecision]:
    """Return the recent tier decisions, newest first."""
    from aegis.governance.models import AuditLog  # noqa: PLC0415

    from app.data import get_sessionmaker  # noqa: PLC0415

    try:
        async with get_sessionmaker()() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLog)
                        .where(AuditLog.action == RISK_AUDIT_ACTION)
                        .order_by(AuditLog.id.desc())
                        .limit(AUDIT_TAIL)
                    )
                )
                .scalars()
                .all()
            )
    except Exception:  # noqa: BLE001 - the console renders without the database
        logger.warning("MCP console: could not read the decision trail", exc_info=True)
        return []
    out: list[MCPDecision] = []
    for row in rows:
        payload = row.payload or {}
        created = row.ts
        stamp = created.replace(tzinfo=UTC) if created.tzinfo is None else created
        out.append(
            MCPDecision(
                at=stamp.astimezone(UTC).isoformat(),
                actor=row.actor or "unknown",
                tool=str(payload.get("tool", "")),
                risk_before=str(payload.get("risk_before", "")),
                risk_after=str(payload.get("risk_after", "")),
                personas_before=list(payload.get("personas_before", []) or []),
                personas_after=list(payload.get("personas_after", []) or []),
                reason=str(payload.get("reason", "")),
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Projection
# ─────────────────────────────────────────────────────────────────────────────


def _self_endpoint() -> str | None:
    """Return this deployment's own MCP endpoint URL, or ``None``.

    Read from configuration rather than assembled from a guessed path: the server half
    (§10.4) chooses where it mounts, and a console that guessed would show a working
    address for a server that is not there. ``None`` is the honest answer when nothing
    is configured, and the console renders it as a stated absence.
    """
    value = str(getattr(get_settings(), "mcp_server_url", "") or "").strip()
    return value or None


def _gate_risk() -> RiskLevel:
    """Return the platform's gate floor, so the console states the tier it names.

    Read from the agent config rather than restated here: a console that hardcoded
    "HIGH stops at the gate" would keep saying it after a deployment lowered the floor,
    and the sentence beside a risk tier is the one an operator acts on.
    """
    from aegis.agent.deps import AgentConfig  # noqa: PLC0415

    return RiskLevel(AgentConfig().gate_min_risk)


def _aegis_tools() -> list[AegisToolRow]:
    """Project the adapter's own registry — read-only, with where each tier is declared.

    Shown here because "which tools exist and what does each cost you" is one question,
    and answering half of it invites the reader to assume the other half is missing.
    Not editable here because a ``ToolSpec.risk`` is domain knowledge versioned with the
    code: making it runtime state is exactly what the retargeting seam work forbade.
    """
    return [
        AegisToolRow(
            name=name,
            description=spec.description,
            risk=spec.risk,
            personas=sorted(
                persona for persona, allowed in ALLOWLIST.items() if name in allowed
            ),
            declared_in="app.adapter.tools.TOOL_REGISTRY",
        )
        for name, spec in sorted(TOOL_REGISTRY.items())
    ]


async def _console(probe: MCPProbe | None = None) -> MCPConsole:
    """Project the process registry and the durable rows into the one response."""
    registry = get_registry()
    tools = registry.tools(include_disabled=True)
    grants = {grant.qualified_name: grant for grant in registry.grants()}
    meta = await _stored_credential_meta()

    rows = [
        MCPToolRow(
            name=tool.qualified_name,
            server_id=tool.server_id,
            remote_name=tool.remote_name,
            description=tool.description,
            risk=RiskLevel.HIGH
            if tool.qualified_name not in grants
            else grants[tool.qualified_name].risk,
            risk_is_default=tool.qualified_name not in grants
            or grants[tool.qualified_name].risk is RiskLevel.HIGH,
            personas=sorted(
                grants[tool.qualified_name].personas
                if tool.qualified_name in grants
                else ()
            ),
            reason=grants[tool.qualified_name].reason if tool.qualified_name in grants else "",
            callable_now=registry.tool(tool.qualified_name) is not None,
        )
        for tool in tools
    ]
    by_server: dict[str, list[MCPToolRow]] = {}
    for row in rows:
        by_server.setdefault(row.server_id, []).append(row)

    servers = [
        MCPServerRow(
            server_id=spec.server_id,
            label=spec.label or spec.server_id,
            url=spec.url,
            auth_header=spec.auth_header,
            enabled=spec.enabled,
            has_credential=registry.has_credential(spec.server_id),
            credential_fingerprint=(
                registry.credential_fingerprint_for(spec.server_id)
                or meta.get(spec.server_id, ("", None))[0]
            ),
            credential_set_by=meta.get(spec.server_id, ("", None))[1],
            discovered_tools=len(by_server.get(spec.server_id, [])),
            granted_tools=sum(
                1 for row in by_server.get(spec.server_id, []) if row.personas
            ),
        )
        for spec in registry.servers()
    ]
    return MCPConsole(
        servers=servers,
        tools=rows,
        aegis_tools=_aegis_tools(),
        decisions=await _decisions(),
        personas=sorted(ALLOWLIST),
        gate_risk=_gate_risk(),
        self_endpoint=_self_endpoint(),
        probe=probe,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@mcp_router.get(
    "/mcp/console",
    response_model=MCPConsole,
    tags=["mcp"],
    summary="External MCP servers, their tools, and the tier each is gated at",
)
async def mcp_console(
    auth: AuthContext = Depends(require_platform_admin),
) -> MCPConsole:
    """Return the whole MCP control plane in one response.

    Args:
        auth: The authenticated platform admin.

    Returns:
        The console aggregate.
    """
    del auth  # authorisation only; the plane is process-wide, not tenant-scoped
    return await _console()


@mcp_router.post(
    "/mcp/servers",
    response_model=MCPConsole,
    status_code=status.HTTP_201_CREATED,
    tags=["mcp"],
    summary="Declare an external MCP server",
)
async def create_server(
    body: ServerCreate,
    auth: AuthContext = Depends(require_platform_admin),
) -> MCPConsole:
    """Declare a peer. Declaring discovers nothing and grants nothing.

    Args:
        body: The connection. Its ``credential`` is held in this process and never
            written to the database or returned.
        auth: The authenticated platform admin.

    Returns:
        The console aggregate, after the write.

    Raises:
        HTTPException: 400 when the id is unusable as a namespace, 409 when it is
            already declared. Re-pointing an existing id is refused rather than
            accepted, because it would silently re-aim every grant written against
            that namespace at a different peer.
    """
    registry = get_registry()
    spec = ExternalServerSpec(
        server_id=body.server_id.strip(),
        url=body.url.strip(),
        label=body.label.strip() or body.server_id.strip(),
        auth_header=body.auth_header.strip() or "Authorization",
        enabled=body.enabled,
    )
    try:
        registry.register_server(spec)
    except ValueError as exc:
        code = (
            status.HTTP_409_CONFLICT
            if "already declared" in str(exc)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    if body.credential:
        registry.set_credential(spec.server_id, body.credential)
    await _persist(spec, actor=auth.username, secret=body.credential or None)
    await _audit(
        SERVER_AUDIT_ACTION,
        auth.username,
        {
            "change": "declared",
            "server_id": spec.server_id,
            "url": spec.url,
            "enabled": spec.enabled,
            # The fingerprint, never the credential — this row is read by humans.
            "credential_fingerprint": (
                credential_fingerprint(body.credential) if body.credential else ""
            ),
        },
    )
    return await _console()


@mcp_router.put(
    "/mcp/servers/{server_id}",
    response_model=MCPConsole,
    tags=["mcp"],
    summary="Edit a connection, including enabling or disabling it",
)
async def update_server(
    body: ServerUpdate,
    server_id: str = Path(description="A declared external MCP server id."),
    auth: AuthContext = Depends(require_platform_admin),
) -> MCPConsole:
    """Edit a peer. Disabling it removes its tools from every agent's payload.

    Args:
        body: The fields to change; ``null`` leaves one alone.
        server_id: The declared peer.
        auth: The authenticated platform admin.

    Returns:
        The console aggregate, after the write.

    Raises:
        HTTPException: 404 when the server is not declared.
    """
    registry = get_registry()
    try:
        before = registry.server(server_id)
        spec = registry.update_server(
            server_id,
            label=body.label,
            url=body.url,
            auth_header=body.auth_header,
            enabled=body.enabled,
        )
    except UnknownExternalServerError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No external MCP server {server_id!r} is declared.",
        ) from exc
    if body.credential is not None:
        registry.set_credential(server_id, body.credential or None)
    await _persist(spec, actor=auth.username, secret=body.credential)
    await _audit(
        SERVER_AUDIT_ACTION,
        auth.username,
        {
            "change": "edited",
            "server_id": server_id,
            "enabled_before": before.enabled,
            "enabled_after": spec.enabled,
            "url": spec.url,
            "credential_rotated": bool(body.credential),
        },
    )
    return await _console()


@mcp_router.delete(
    "/mcp/servers/{server_id}",
    response_model=MCPConsole,
    tags=["mcp"],
    summary="Forget a connection, with its tools, grants and credential",
)
async def delete_server(
    server_id: str = Path(description="A declared external MCP server id."),
    auth: AuthContext = Depends(require_platform_admin),
) -> MCPConsole:
    """Remove a peer entirely.

    Args:
        server_id: The declared peer.
        auth: The authenticated platform admin.

    Returns:
        The console aggregate, after the removal.

    Raises:
        HTTPException: 404 when the server is not declared.
    """
    registry = get_registry()
    try:
        registry.server(server_id)
    except UnknownExternalServerError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No external MCP server {server_id!r} is declared.",
        ) from exc
    registry.remove_server(server_id)
    await _forget(server_id)
    await _audit(
        SERVER_AUDIT_ACTION, auth.username, {"change": "removed", "server_id": server_id}
    )
    return await _console()


@mcp_router.post(
    "/mcp/servers/{server_id}/test",
    response_model=MCPConsole,
    tags=["mcp"],
    summary="Test the connection and re-read the peer's tool list",
)
async def test_server(
    server_id: str = Path(description="A declared external MCP server id."),
    auth: AuthContext = Depends(require_platform_admin),
) -> MCPConsole:
    """Connect to ``server_id``, report what it said, and refresh its tools.

    The probe half never raises: an unreachable peer answers with ``reachable: false``
    and its own sentence, because "why not" is the useful half of a test button.
    Discovery only runs when the probe succeeded, and it *replaces* that peer's tools
    rather than merging, so a tool the peer has withdrawn stops being offered. Grants
    over a withdrawn tool are left in place but authorise nothing —
    :meth:`~app.mcp.client.ExternalToolRegistry.is_allowed` requires a currently visible
    tool — so re-adding it upstream cannot silently re-arm an old decision.

    Args:
        server_id: The declared peer.
        auth: The authenticated platform admin.

    Returns:
        The console aggregate with ``probe`` set.

    Raises:
        HTTPException: 404 when the server is not declared; 409 when a tool it
            advertises would collide with a registered Aegis tool.
    """
    del auth
    registry = get_registry()
    try:
        result = await registry.probe(server_id)
    except UnknownExternalServerError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No external MCP server {server_id!r} is declared in this deployment. "
                "Add it first, or declare it in AEGIS_MCP_CLIENT_SERVERS."
            ),
        ) from exc
    if result.reachable:
        try:
            await registry.discover(server_id)
        except ExternalToolCollisionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
    return await _console(
        probe=MCPProbe(
            server_id=result.server_id,
            reachable=result.reachable,
            server_name=result.server_name,
            protocol_version=result.protocol_version,
            tools=list(result.tools),
            detail=result.detail,
        )
    )


@mcp_router.put(
    "/mcp/tools/{tool_name}/grant",
    response_model=MCPConsole,
    tags=["mcp"],
    summary="Admit an external tool for personas, at a risk tier",
)
async def write_grant(
    body: GrantWrite,
    tool_name: str = Path(description="The qualified external tool name."),
    auth: AuthContext = Depends(require_platform_admin),
) -> MCPConsole:
    """Admit, re-scope or revoke one external tool, and record the decision.

    The audit row carries the tier **before** and **after**, the persona sets before and
    after, the actor and the reason. Lowering a gate is the action a post-incident
    review reads first, and "it is HIGH now" is not an answer to "who lowered it, when,
    and what did they say".

    Args:
        body: The personas admitted, the tier, and why.
        tool_name: The qualified name — it must be in the external namespace and must
            already have been discovered.
        auth: The authenticated platform admin.

    Returns:
        The console aggregate, after the write.

    Raises:
        HTTPException: 400 when the name is not an external one or names a persona the
            adapter does not define; 404 when no declared server advertises it.
    """
    registry = get_registry()
    if not is_external_name(tool_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{tool_name!r} is not an external MCP tool. This plane governs the "
                "external namespace only; an in-process tool's tier is declared on its "
                "ToolSpec, in app.adapter.tools, where it is reviewed with the code."
            ),
        )
    unknown = sorted(set(body.personas) - set(ALLOWLIST))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No such persona: {unknown}. A grant may only name a persona the "
                f"adapter defines ({sorted(ALLOWLIST)})."
            ),
        )

    previous = registry.grant_for(tool_name)
    risk_before = (previous.risk if previous else RiskLevel.HIGH).value
    personas_before = sorted(previous.personas) if previous else []

    if not body.personas:
        registry.revoke(tool_name)
        risk_after = RiskLevel.HIGH.value
    else:
        try:
            registry.grant(
                tool_name,
                frozenset(body.personas),
                risk=body.risk,
                reason=body.reason,
            )
        except UnknownToolError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"{tool_name!r} has not been discovered on any declared MCP server, "
                    "so it cannot be admitted. Test the connection first."
                ),
            ) from exc
        risk_after = body.risk.value

    await _audit(
        RISK_AUDIT_ACTION,
        auth.username,
        {
            "tool": tool_name,
            "risk_before": risk_before,
            "risk_after": risk_after,
            "personas_before": personas_before,
            "personas_after": sorted(body.personas),
            "reason": body.reason,
            "gate_floor": _gate_risk().value,
        },
    )
    if body.personas and body.risk is not RiskLevel.HIGH:
        # Worth a log line of its own as well as the audit row: this is the one write in
        # the product that moves an action *out* of the human gate.
        logger.warning(
            "MCP console: %s set %s to %s risk (was %s) — %s",
            auth.username,
            tool_name,
            risk_after,
            risk_before,
            body.reason or "no reason given",
        )
    return await _console()


# ─────────────────────────────────────────────────────────────────────────────
# Mounting
# ─────────────────────────────────────────────────────────────────────────────


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target`` as real ``APIRoute`` objects.

    Idempotent, like :func:`app.api.routes_redteam.mount` and for the same reason: this
    plane is mounted from the composition root while :mod:`app.api.routes` is edited
    elsewhere, and mounting twice would leave a second, shadowed copy of every handler
    in the served table.

    Args:
        target: The application's main router, extended in place.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    target.routes.extend(
        route
        for route in mcp_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
