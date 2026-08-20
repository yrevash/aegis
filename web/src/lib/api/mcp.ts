/**
 * The MCP control plane — external tool servers, their tiers, and Aegis's own endpoint.
 *
 * A faithful TypeScript mirror of `MCPConsole` / `ServerCreate` / `ServerUpdate` /
 * `GrantWrite` in `backend/src/app/api/routes_mcp.py`.
 *
 * **Why this module carries its own fetch instead of `client.ts`'s `request`.** Same
 * reason `redteam.ts` does: that helper discards the response body, and on these routes
 * the body *is* the decision. A 403 says in a sentence that only a platform admin may
 * decide which third party's code an agent reaches; a 409 names the tool whose name
 * would have collided with an Aegis one; a failed connection test carries the peer's own
 * error. Collapsing those into "request failed" would delete the governance the surface
 * exists to show.
 *
 * **There is no credential field on any response type, anywhere in this file.** Not
 * "omitted" — absent, so no later edit can start rendering one. A secret typed into the
 * connection form travels one way; what comes back is a twelve-character fingerprint.
 *
 * The MCP *protocol* client is deliberately not here — see
 * `components/mcp/AegisMcpPanel.tsx`, which speaks Streamable HTTP to Aegis's own server
 * with the official `@modelcontextprotocol/sdk`. This module is the ordinary HTTP
 * control plane over the *external* registry, which is not an MCP conversation at all.
 *
 * @see backend/src/app/api/routes_mcp.py
 * @see backend/src/app/mcp/client.py
 */

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

/** The three tiers a tool can be gated at. Mirrors `aegis.core.types.RiskLevel`. */
export type McpRisk = 'low' | 'medium' | 'high'

/** One declared external MCP server. */
export interface McpServerRow {
  serverId: string
  label: string
  /** The peer's Streamable HTTP endpoint; empty when the peer is supplied in process. */
  url: string
  /** The header this peer's credential is sent in — `Authorization`, `X-API-Key`, … */
  authHeader: string
  /** False means its tools leave the agent's payload entirely, not merely get refused. */
  enabled: boolean
  /** Whether the serving process currently holds a credential for this peer. */
  hasCredential: boolean
  /** Twelve hex characters of SHA-256, or empty. Never the credential. */
  credentialFingerprint: string
  /** Who last set the credential through the console, when anyone did. */
  credentialSetBy: string | null
  discoveredTools: number
  grantedTools: number
}

/** One external tool, its tier, and who may call it. */
export interface McpToolRow {
  /** The qualified Aegis-side name, e.g. `mcp__acme__search`. */
  name: string
  serverId: string
  /** The name the peer knows it by — the only name that goes on the wire. */
  remoteName: string
  /** The peer's description, after the TOOL_RESULT rail screened it. */
  description: string
  risk: McpRisk
  /** True when the tier is the untouched HIGH default rather than somebody's decision. */
  riskIsDefault: boolean
  /** Persona ids admitted. Empty means nobody may call it. */
  personas: string[]
  /** Why the tier is what it is; empty when it was never set. */
  reason: string
  /** False when the owning server is disabled — the tool is not offered to any agent. */
  callableNow: boolean
}

/** One of Aegis's own tools. Shown here, never editable here. */
export interface AegisToolRow {
  name: string
  description: string
  risk: McpRisk
  personas: string[]
  /** The module the tier is declared in, so a reader can go and check it. */
  declaredIn: string
}

/** One recorded tier decision — the before, the after, the actor and the reason. */
export interface McpDecision {
  at: string
  actor: string
  tool: string
  riskBefore: string
  riskAfter: string
  personasBefore: string[]
  personasAfter: string[]
  reason: string
}

/** What a peer answered when the console tested the connection. */
export interface McpProbe {
  serverId: string
  reachable: boolean
  serverName: string
  protocolVersion: string
  /** Remote tool names, in the peer's own namespace. */
  tools: string[]
  /** The peer's own failure sentence, or empty. */
  detail: string
}

/** Body of every route in this plane. One aggregate, one consistent picture. */
export interface McpConsole {
  servers: McpServerRow[]
  tools: McpToolRow[]
  aegisTools: AegisToolRow[]
  decisions: McpDecision[]
  /** The persona ids a grant may name — read from the adapter's own allowlist. */
  personas: string[]
  /** The tier at or above which a call stops at the human gate. */
  gateRisk: McpRisk
  /** Aegis's own MCP endpoint, or null when this deployment configured none. */
  selfEndpoint: string | null
  /** Set only on a test-connection response. */
  probe?: McpProbe | null
}

/** A new external MCP connection. `credential` travels one way and never comes back. */
export interface McpServerCreate {
  serverId: string
  label?: string
  url?: string
  authHeader?: string
  credential?: string
  enabled?: boolean
}

/** An edit to an existing connection. An omitted field is left alone. */
export interface McpServerUpdate {
  label?: string
  url?: string
  authHeader?: string
  enabled?: boolean
  /** A new secret, or `''` to forget the one the serving process holds. */
  credential?: string
}

/** A platform admin's decision about one external tool. */
export interface McpGrantWrite {
  /** Persona ids admitted. An empty array revokes the grant. */
  personas: string[]
  risk: McpRisk
  reason: string
}

/** An MCP control-plane failure that kept the server's own explanation. */
export class McpApiError extends ApiError {
  constructor(status: number, method: string, path: string, detail?: string) {
    super(status, method, path, detail)
    this.name = 'McpApiError'
  }
}

async function call<T>(path: string, init: RequestInit, token: string | null): Promise<T> {
  const method = init.method ?? 'GET'
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    const detail = await res
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined)
    if (res.status === 401) reportSessionExpired()
    throw new McpApiError(res.status, method, path, detail)
  }
  return (await res.json()) as T
}

/** The declared peers, their tools, the tier each is gated at, and this deployment's endpoint. */
export async function getMcpConsole(token: string | null): Promise<McpConsole> {
  return call<McpConsole>('/mcp/console', { method: 'GET' }, token)
}

/** Declare a peer. Declaring discovers nothing and grants nothing. */
export async function createMcpServer(
  token: string | null,
  body: McpServerCreate,
): Promise<McpConsole> {
  return call<McpConsole>('/mcp/servers', { method: 'POST', body: JSON.stringify(body) }, token)
}

/** Edit a peer — including enabling or disabling it, and rotating its credential. */
export async function updateMcpServer(
  token: string | null,
  serverId: string,
  body: McpServerUpdate,
): Promise<McpConsole> {
  return call<McpConsole>(
    `/mcp/servers/${encodeURIComponent(serverId)}`,
    { method: 'PUT', body: JSON.stringify(body) },
    token,
  )
}

/** Forget a peer, with its tools, its grants and its credential. */
export async function deleteMcpServer(
  token: string | null,
  serverId: string,
): Promise<McpConsole> {
  return call<McpConsole>(
    `/mcp/servers/${encodeURIComponent(serverId)}`,
    { method: 'DELETE' },
    token,
  )
}

/**
 * Test the connection and re-read the peer's tool list.
 *
 * An explicit act: it opens a connection to a third party, so it does not happen when
 * the page loads. An unreachable peer is a 200 with `probe.reachable === false` and the
 * peer's own sentence — "why not" is the useful half of a test button.
 */
export async function testMcpServer(
  token: string | null,
  serverId: string,
): Promise<McpConsole> {
  return call<McpConsole>(
    `/mcp/servers/${encodeURIComponent(serverId)}/test`,
    { method: 'POST' },
    token,
  )
}

/** Admit, re-scope or revoke one external tool. An empty `personas` array is the revocation. */
export async function writeMcpGrant(
  token: string | null,
  toolName: string,
  body: McpGrantWrite,
): Promise<McpConsole> {
  return call<McpConsole>(
    `/mcp/tools/${encodeURIComponent(toolName)}/grant`,
    { method: 'PUT', body: JSON.stringify(body) },
    token,
  )
}
