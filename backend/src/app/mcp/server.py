"""A **real** MCP stdio server that fronts the adapter tool registry.

This module turns the platform's in-process action tools
(:data:`app.adapter.tools.TOOL_REGISTRY`) into first-class **Model Context
Protocol** tools an external client (Claude Desktop, ``mcp`` CLI, any MCP host)
can list and call over stdio. It is built against the installed ``mcp`` SDK
(2.x) low-level :class:`mcp.server.Server`, whose ``on_list_tools`` /
``on_call_tool`` handlers we wire to the facade below.

Facade, **not** a bypass
------------------------
The server never re-implements a tool or reaches around the platform's controls.
Every surface delegates to the same functions the in-process agent uses:

* **Tool list** — :func:`app.adapter.tools.tool_definitions_for` returns the
  *allowlist-filtered* OpenAI/MCP function schemas for the configured persona;
  each becomes an MCP :class:`~mcp.types.Tool` with the tool's real name,
  description and JSON-schema (straight from :meth:`ToolSpec.definition`). A tool
  a persona may not call is never listed.
* **Tool call** — routed through :func:`app.adapter.tools.run_tool`, which
  **re-checks the allowlist before any side effect** and emits an **audit** row
  via the injected sink (:func:`app.data.record_audit` in production). The MCP
  layer adds no privilege of its own.

Risk policy (security)
----------------------
Risk tiers are preserved exactly as the in-process human-in-the-loop gate would
apply them:

* **LOW / MEDIUM** (reads and low-consequence writes) execute through
  :func:`run_tool` and return the real :class:`ToolActionResult`.
* **HIGH** (consequential, customer-visible writes such as
  ``update_request_status``) are **listed** so a client can discover them, but a
  CALL is **not auto-executed**. It returns a clear "requires human approval —
  routed to the approval inbox; not auto-executed via MCP" result and performs
  **no side effect**. This mirrors the platform's bounded-autonomy gate: an MCP
  client is a proposer, not an approver.

Unknown or not-allowlisted tool names are rejected with an error result before
any execution or audit.

The :mod:`mcp` SDK is imported lazily inside functions/methods so importing
:mod:`app.mcp.server` (and the rest of the app) never requires the SDK.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:  # Type-only imports — never require the SDK at import time.
    import mcp.types as mcp_types
    from mcp.server import Server, ServerRequestContext

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

SERVER_NAME = "tcs-adapter-tools"
"""MCP server name advertised to clients."""

SERVER_VERSION = "1.0.0"
"""MCP server version advertised to clients."""

MCP_ACTOR = "mcp"
"""Actor id attributed to every action initiated through this MCP facade."""

PERSONA_ENV_VAR = "MCP_PERSONA_ID"
"""Environment variable overriding the persona whose allowlist the server uses."""

APPROVAL_REQUIRED_MESSAGE = (
    "This HIGH-risk write is a proposal only — it was NOT auto-executed via MCP "
    "and requires human approval through the platform's bounded-autonomy gate. The "
    "proposal has been recorded in the audit log. A high-risk write is a proposal "
    "here, not a decision; a human must approve it at the bounded-autonomy gate."
)
"""Human-facing explanation returned when a HIGH-risk tool is called over MCP."""

class _AuditDefault:
    """Sentinel type meaning "resolve the platform's real audit sink lazily"."""


_USE_DEFAULT_AUDIT = _AuditDefault()
"""Sentinel: resolve the platform's real ``record_audit`` sink lazily."""


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
# The testable facade
# ─────────────────────────────────────────────────────────────────────────────


class AdapterToolServer:
    """Maps the adapter tool registry onto MCP list/call semantics.

    This is the unit-testable core of the server: :meth:`list_tools` and
    :meth:`call_tool` are the exact functions the registered MCP handlers invoke,
    so tests can drive them directly with no live stdio client.

    Attributes:
        persona_id: The persona whose allowlist scopes the exposed tools.
        store: The record store tool executions read and write.
        actor: The audit actor attributed to MCP-initiated actions.
    """

    def __init__(
        self,
        persona_id: str | None = None,
        store: RecordStore | None = None,
        *,
        audit: AuditFn | None | _AuditDefault = _USE_DEFAULT_AUDIT,
        actor: str = MCP_ACTOR,
    ) -> None:
        """Configure the facade.

        Args:
            persona_id: Persona whose allowlist to enforce. Defaults to the
                ``MCP_PERSONA_ID`` env var, then :data:`DEFAULT_PERSONA_ID`.
            store: Record store to run tools against. Defaults to an in-memory
                store seeded from :func:`generate_synthetic_sync`.
            audit: Audit sink. Defaults to the platform's ``record_audit`` (so
                the trail is preserved); tests may inject a capturing fake.
            actor: Audit actor id for MCP-initiated actions.
        """
        self.persona_id = persona_id or os.getenv(PERSONA_ENV_VAR) or DEFAULT_PERSONA_ID
        self.store = (
            store
            if store is not None
            else InMemoryRecordStore.from_dataset(generate_synthetic_sync())
        )
        self._audit: AuditFn | None = (
            _resolve_default_audit() if isinstance(audit, _AuditDefault) else audit
        )
        self.actor = actor

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
        import mcp.types as mcp_types  # noqa: PLC0415

        tools: list[mcp_types.Tool] = []
        for definition in tool_definitions_for(self.persona_id):
            function = definition["function"]
            tools.append(
                mcp_types.Tool(
                    name=function["name"],
                    description=function["description"],
                    inputSchema=function["parameters"],
                )
            )
        return tools

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
            return self._error_result(f"Unknown tool {name!r}.")
        if not is_allowed(self.persona_id, name):
            return self._error_result(
                f"Persona {self.persona_id!r} is not allowed to call tool {name!r}."
            )

        # 2) Preserve risk semantics: HIGH-risk writes are proposals over MCP —
        #    do NOT execute the side effect; route to the human approval gate.
        spec = TOOL_REGISTRY[name]
        if spec.risk is RiskLevel.HIGH:
            await self._audit_high_risk_proposal(name, arguments)
            return self._approval_required_result(name)

        # 3) LOW/MEDIUM: execute through the REAL run_tool (allowlist + audit).
        try:
            result = await run_tool(self.persona_id, name, arguments, self._tool_context())
        except (UnknownToolError, ToolNotAllowedError) as exc:  # defence in depth
            return self._error_result(str(exc))
        except Exception as exc:  # noqa: BLE0001 — validation errors → error result
            return self._error_result(f"{type(exc).__name__}: {exc}")

        return self._ok_result(result.model_dump(mode="json"), is_error=not result.ok)

    # -- result builders -------------------------------------------------------

    def _ok_result(
        self, payload: dict[str, Any], *, is_error: bool
    ) -> mcp_types.CallToolResult:
        """Wrap a JSON payload as a text + structured MCP result."""
        import mcp.types as mcp_types  # noqa: PLC0415

        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=json.dumps(payload))],
            structured_content=payload,
            is_error=is_error,
        )

    async def _audit_high_risk_proposal(
        self, name: str, arguments: dict[str, Any]
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
                         "risk": RiskLevel.HIGH.value},
            )
        except Exception:  # noqa: BLE001 - audit is best-effort, never gates the result
            logger.warning("MCP high-risk-proposal audit failed for %s", name, exc_info=True)

    def _approval_required_result(self, name: str) -> mcp_types.CallToolResult:
        """Build the "requires human approval / not executed" MCP result."""
        payload = {
            "status": "requires_approval",
            "tool": name,
            "risk": RiskLevel.HIGH.value,
            "executed": False,
            "message": APPROVAL_REQUIRED_MESSAGE,
        }
        # A gated high-risk proposal is a legitimate outcome, not a protocol
        # error, so is_error stays False.
        return self._ok_result(payload, is_error=False)

    def _error_result(self, message: str) -> mcp_types.CallToolResult:
        """Build an ``is_error`` MCP result carrying ``message``."""
        import mcp.types as mcp_types  # noqa: PLC0415

        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=message)],
            is_error=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# MCP server assembly + entrypoint
# ─────────────────────────────────────────────────────────────────────────────


def build_server(
    persona_id: str | None = None,
    store: RecordStore | None = None,
    *,
    audit: AuditFn | None | _AuditDefault = _USE_DEFAULT_AUDIT,
) -> Server:
    """Build a real :class:`mcp.server.Server` fronting the tool registry.

    The returned server has its ``tools/list`` and ``tools/call`` handlers wired
    to an :class:`AdapterToolServer`, which is also attached as ``server.facade``
    for inspection and testing.

    Args:
        persona_id: Persona whose allowlist scopes the tools (see the facade).
        store: Record store to run tools against (see the facade).
        audit: Audit sink (see the facade).

    Returns:
        A configured low-level MCP :class:`~mcp.server.Server`.
    """
    import mcp.types as mcp_types  # noqa: PLC0415
    from mcp.server import Server  # noqa: PLC0415

    facade = AdapterToolServer(persona_id=persona_id, store=store, audit=audit)

    async def on_list_tools(
        _ctx: ServerRequestContext[Any],
        _params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(tools=facade.list_tools())

    async def on_call_tool(
        _ctx: ServerRequestContext[Any],
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        return await facade.call_tool(params.name, params.arguments)

    server: Server = Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=(
            "Adapter action tools for the service-request domain. HIGH-risk "
            "writes are gated behind human approval and are not auto-executed."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    server.facade = facade  # type: ignore[attr-defined]  # convenience handle
    return server


async def run_stdio(server: Server) -> None:
    """Serve ``server`` over the MCP stdio transport until the client closes it.

    Args:
        server: A server from :func:`build_server`.
    """
    from mcp.server.stdio import stdio_server  # noqa: PLC0415

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Run the MCP stdio server (the ``python -m app.mcp.server`` entrypoint)."""
    import anyio  # noqa: PLC0415  # the mcp SDK's async runtime

    anyio.run(run_stdio, build_server())


if __name__ == "__main__":
    main()
