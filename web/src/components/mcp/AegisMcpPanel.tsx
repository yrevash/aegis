'use client'

import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js'
import { Loader2, Plug, ShieldCheck } from 'lucide-react'
import { useCallback, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Button } from '@/components/primitives/button'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState } from '@/components/primitives/States'
import { errorSentence } from '@/lib/api/apiError'

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
        detail: errorSentence(
          error,
          'The MCP handshake did not complete. Check the endpoint is serving Streamable HTTP.',
        ),
      })
    } finally {
      await client.close().catch(() => undefined)
    }
  }, [endpoint, token])

  if (!endpoint) {
    return (
      <Card className="rounded-lg">
        <CardHeader title="Aegis as an MCP server" eyebrow="the other direction · what this deployment offers" />
        <CardBody>
          <EmptyState
            icon={Plug}
            title="This deployment publishes no MCP address"
            body="Set AEGIS_MCP_SERVER_URL to the address this deployment serves Streamable HTTP on. Nothing is guessed here: a made-up URL would render as a live address for a server that is not running."
          />
        </CardBody>
      </Card>
    )
  }

  return (
    <Card className="rounded-lg">
      <CardHeader
        title="Aegis as an MCP server"
        eyebrow="the other direction · what this deployment offers"
        actions={
          <span className="flex items-center gap-2">
          <InfoTip label="What this panel proves">
            The console connects as a real MCP client — the official protocol SDK over
            Streamable HTTP, the same client any other host would use — carrying your own
            bearer on every request. The list it returns is what the server offers{' '}
            <em>you</em>: another principal connecting to the same address sees a different
            one. There is no “call it” button, because a call belongs on the agent’s path,
            behind the human gate.
          </InfoTip>
          <Button
            type="button"
            onClick={() => void connect()}
            disabled={connection.state === 'connecting'}
          >
            {connection.state === 'connecting' ? (
              <>
                <Loader2
                  className="mr-2 size-4 animate-spin motion-reduce:animate-none"
                  aria-hidden
                />
                Connecting
              </>
            ) : (
              <>
                <Plug className="mr-2 size-4" aria-hidden />
                {connection.state === 'connected' ? 'Reconnect' : 'Connect'}
              </>
            )}
          </Button>
          </span>
        }
      />
      <CardBody className="space-y-4">
        {connection.state === 'failed' ? (
          <ErrorState error={connection.detail} retry={() => void connect()} />
        ) : null}

        {connection.state === 'connected' ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="ok" className="gap-1">
                <ShieldCheck className="size-3" aria-hidden />
                connected
              </Badge>
              <Figure className="text-muted-foreground">
                {`${connection.serverName}${connection.version ? ` ${connection.version}` : ''}`}
              </Figure>
            </div>
            {connection.tools.length === 0 ? (
              <EmptyState
                icon={ShieldCheck}
                title="The server offered you no tools"
                body="That is an answer, not a failure: the tool list is a function of who is asking, and this principal is admitted to none of them."
              />
            ) : (
              <Table>
                <THead>
                  <TH>Tool</TH>
                  <TH>What it does</TH>
                </THead>
                <TBody>
                  {connection.tools.map((tool) => (
                    <TR key={tool.name}>
                      <TD className="whitespace-nowrap">
                        <Figure className="text-foreground">{tool.name}</Figure>
                      </TD>
                      <TD className="text-sm text-muted-foreground">
                        {tool.description || tool.title || '—'}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
            <Receipt
              origin={`tools/list · Streamable HTTP · ${endpoint}`}
              detail="scoped to your principal by the server, so another caller sees a different list"
            />
          </>
        ) : null}
      </CardBody>
    </Card>
  )
}
