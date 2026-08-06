"""Model Context Protocol (MCP) facade over the adapter tool registry.

This package exposes the platform's real action-tool registry
(:data:`app.adapter.tools.TOOL_REGISTRY`) to external MCP clients — e.g. Claude
Desktop — over a genuine MCP **stdio** server, while preserving the platform's
governance: the per-persona **allowlist**, the immutable **audit** trail, and the
**risk tiers** that gate consequential writes behind human approval.

The heavy :mod:`mcp` SDK is imported **lazily** inside :mod:`app.mcp.server`, so
importing :mod:`app.mcp` never requires the SDK to be installed. Build/run the
server via :func:`app.mcp.server.build_server` / ``python -m app.mcp.server``.
"""

from __future__ import annotations

__all__ = ["server"]
