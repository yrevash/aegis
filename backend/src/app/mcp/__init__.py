"""Model Context Protocol (MCP) server — multi-tenant, over Streamable HTTP.

This package exposes the platform's real action-tool registry
(:data:`app.adapter.tools.TOOL_REGISTRY`) plus Aegis's own tenant-scoped platform
reads to external MCP clients — e.g. Claude Desktop — while preserving the
platform's governance: the per-persona **allowlist**, the immutable **audit**
trail, the **risk tiers** that gate consequential writes behind human approval,
and — since §10.4/10.5 — **per-call identity, RBAC and tenant scope**.

The transport is the SDK's Streamable HTTP app (:func:`app.mcp.server.build_http_app`),
mounted by :func:`app.main.create_app` at ``/mcp``. The stdio entrypoint is gone: it
had no caller identity to enforce anything against, and its persona was pinned by an
environment variable, which made one process serve exactly one tenant.

The heavy :mod:`mcp` SDK is imported **lazily** inside :mod:`app.mcp.server`, so
importing :mod:`app.mcp` never requires the SDK to be installed.
"""

from __future__ import annotations

__all__ = ["server"]
