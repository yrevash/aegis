'use client'

import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js'
import { AlertTriangle, Loader2, Plug, ShieldCheck } from 'lucide-react'
import { useCallback, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Button } from '@/components/primitives/button'

/**
 * The admin's own MCP client, speaking the protocol to Aegis's own server (§10.7).
 *
 * **The official SDK, not a fetch wrapper.** `@modelcontextprotocol/sdk`'s `Client` over
 * `StreamableHTTPClientTransport` is what connects here — the same client Claude Desktop
 * or any other host would use. The reasoning is the one that deleted 696 hand-written
 * lines in §8.7: a hand-rolled protocol client drifts from the spec, and nobody notices
 * until a peer changes. It also makes the demo claim honest — this panel is not a
 * simulation of an MCP conversation, it is one.
 *
 * **What it proves, and what it deliberately does not do.** It initialises a session,
 * carries the operator's own bearer token on every request, and lists the tools the
 * server offers *that caller* — which is the whole point of §10.5: the tool list is a
 * function of who is asking, so an admin and a tenant user connecting to the same URL
 * see different lists. It stops there. There is no "call it" button, because a call
 * belongs on the agent's path, behind the human gate; a console that executed an MCP
 * tool directly would be exactly the side door the phase exists to close.
 *
 * **When the endpoint is not configured** the panel says so and offers nothing. A
 * guessed URL would render as a live address for a server that is not there, which is
 * the one thing worse than an absence.
 */

/** One tool as the protocol described it. */
interface ProtocolTool {
  name: string
  description: string
  title: string
}

/** What the panel knows after (or during) a connection attempt. */
type Connection =
  | { state: 'idle' }
  | { state: 'connecting' }
  | { state: 'connected'; serverName: string; version: string; tools: ProtocolTool[] }
  | { state: 'failed'; detail: string }

export function AegisMcpPanel({
  endpoint,
  token,
}: {
  endpoint: string | null
  token: string | null
}): ReactElement {
  const [connection, setConnection] = useState<Connection>({ state: 'idle' })

  const connect = useCallback(async () => {
    if (!endpoint) return
    setConnection({ state: 'connecting' })
    // A client per attempt, closed in `finally`: a session that outlives the operator's
    // click is a session whose authority nobody is holding any more.
    const client = new Client({ name: 'aegis-admin-console', version: '1.0.0' })
    try {
      const transport = new StreamableHTTPClientTransport(new URL(endpoint, window.location.href), {
        // The operator's own bearer, on every request the transport makes — not once at
        // connect. Scope is per call on the server side (§10.5), and a client that
        // authenticated only the handshake would be assuming the opposite.
        requestInit: token ? { headers: { Authorization: `Bearer ${token}` } } : undefined,
      })
      await client.connect(transport)
      const info = client.getServerVersion()
      const listed = await client.listTools()
      setConnection({
        state: 'connected',
        serverName: info?.name ?? 'unknown',
        version: info?.version ?? '',
        tools: listed.tools.map((tool) => ({
          name: tool.name,
          description: tool.description ?? '',
          title: tool.title ?? '',
        })),
      })
    } catch (error) {
      setConnection({
        state: 'failed',
        detail: error instanceof Error ? error.message : String(error),
      })
    } finally {
      await client.close().catch(() => undefined)
    }
  }, [endpoint, token])

  if (!endpoint) {
    return (
      <Card>
        <CardHeader title="Ask Aegis over MCP" eyebrow="This deployment's own server" />
        <CardBody className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-risk-ink" />
          <div>
            <p className="text-sm text-foreground">
              No MCP endpoint is configured, so there is nothing to connect to.
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Set <code className="font-mono">AEGIS_MCP_SERVER_URL</code> to the address this
              deployment serves Streamable HTTP on. Nothing is guessed here: a made-up URL
              would render as a live address for a server that is not running.
            </p>
          </div>
        </CardBody>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader
        title="Ask Aegis over MCP"
        eyebrow="This deployment's own server"
        actions={
          <Button
            type="button"
            onClick={() => void connect()}
            disabled={connection.state === 'connecting'}
          >
            {connection.state === 'connecting' ? (
              <>
                <Loader2 className="mr-2 size-4 animate-spin" aria-hidden />
                Connecting
              </>
            ) : (
              <>
                <Plug className="mr-2 size-4" aria-hidden />
                {connection.state === 'connected' ? 'Reconnect' : 'Connect'}
              </>
            )}
          </Button>
        }
      />
      <CardBody className="space-y-4">
        <p className="text-sm text-muted-foreground">
          The console connects as a real MCP client, with your own bearer on every request.
          The list below is what the server offers <em>you</em> — another principal
          connecting to the same address sees a different one.
        </p>

        {connection.state === 'failed' ? (
          <div className="flex items-start gap-3 rounded-md border border-border bg-surface-2 p-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-block-ink" aria-hidden />
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">Could not connect</p>
              <p className="mt-1 break-words font-mono text-xs text-muted-foreground">
                {connection.detail}
              </p>
            </div>
          </div>
        ) : null}

        {connection.state === 'connected' ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="ok" className="gap-1">
                <ShieldCheck className="size-3" aria-hidden />
                connected
              </Badge>
              <span className="font-mono text-xs text-muted-foreground">
                {connection.serverName}
                {connection.version ? ` ${connection.version}` : ''}
              </span>
            </div>
            {connection.tools.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                The server offered this principal no tools.
              </p>
            ) : (
              <Table>
                <THead>
                  <TH>Tool</TH>
                  <TH>What it does</TH>
                </THead>
                <TBody>
                  {connection.tools.map((tool) => (
                    <TR key={tool.name}>
                      <TD className="whitespace-nowrap font-mono text-xs">{tool.name}</TD>
                      <TD className="text-sm text-muted-foreground">
                        {tool.description || tool.title || '—'}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
            <p className="border-t border-border pt-2 text-xs text-muted-foreground">
              Source: <span className="font-mono">tools/list</span> over Streamable HTTP at{' '}
              <span className="font-mono">{endpoint}</span>, scoped to your principal by the
              server.
            </p>
          </>
        ) : null}
      </CardBody>
    </Card>
  )
}
