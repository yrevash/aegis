'use client'

import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Card, CardBody } from '@/components/ui/Card'
import { Button } from '@/components/primitives/button'
import { BackendGate } from '@/components/shared/BackendGate'
import {
  createMcpServer,
  deleteMcpServer,
  getMcpConsole,
  testMcpServer,
  updateMcpServer,
  writeMcpGrant,
  type McpConsole,
  type McpGrantWrite,
  type McpProbe,
  type McpServerCreate,
  type McpServerUpdate,
} from '@/lib/api/mcp'
import { useAuth } from '@/lib/auth/AuthContext'

import { AegisMcpPanel } from './AegisMcpPanel'
import { Connections } from './Connections'
import { ToolGovernance } from './ToolGovernance'

/**
 * The admin MCP console (§10.6/10.7) — three questions on one page.
 *
 * *Which external servers may our agents reach?* — {@link Connections}: declare a peer,
 * prove it answers, turn it off, forget it.
 *
 * *What may each tool do, and to whom?* — {@link ToolGovernance}: the tier every tool is
 * gated at, per named tool, with the consequence stated at the moment of the change and
 * the decision trail underneath. Aegis's own tools appear there too, read-only.
 *
 * *What does Aegis itself offer over MCP?* — {@link AegisMcpPanel}, which connects to
 * this deployment's own server with the official protocol SDK.
 *
 * **Every write returns the whole aggregate**, and this component replaces its state with
 * it rather than patching the row it just changed. Optimistic patching is how a console
 * ends up showing a tier the server refused; the response is the truth, always.
 *
 * Nothing here executes an external tool. That path runs through the agent, behind the
 * human gate; a button on this page would be the side door the phase exists to close.
 */
function McpConsoleBody({ token }: { token: string | null }): ReactElement {
  const [data, setData] = useState<McpConsole | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [probe, setProbe] = useState<McpProbe | null>(null)

  /** Run one control-plane write, replacing state with the server's own aggregate. */
  const run = useCallback(
    async (key: string, action: () => Promise<McpConsole>) => {
      setBusy(key)
      try {
        const next = await action()
        setData(next)
        setProbe(next.probe ?? null)
        setError(null)
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught))
      } finally {
        setBusy(null)
      }
    },
    [],
  )

  const load = useCallback(async () => {
    try {
      setData(await getMcpConsole(token))
      setError(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [token])

  useEffect(() => {
    void load()
  }, [load])

  const onCreate = useCallback(
    (body: McpServerCreate) => void run('create', () => createMcpServer(token, body)),
    [run, token],
  )
  const onUpdate = useCallback(
    (serverId: string, body: McpServerUpdate) =>
      void run(serverId, () => updateMcpServer(token, serverId, body)),
    [run, token],
  )
  const onDelete = useCallback(
    (serverId: string) => void run(serverId, () => deleteMcpServer(token, serverId)),
    [run, token],
  )
  const onTest = useCallback(
    (serverId: string) => void run(serverId, () => testMcpServer(token, serverId)),
    [run, token],
  )
  const onWrite = useCallback(
    (name: string, next: McpGrantWrite) =>
      void run(name, () => writeMcpGrant(token, name, next)),
    [run, token],
  )

  if (!data && !error) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-md border border-dashed border-border bg-surface-2 text-sm text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" aria-hidden />
        Reading the MCP registry…
      </div>
    )
  }

  return (
    <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <p className="eyebrow">aegis · mcp</p>
          <h2 className="t-display text-foreground">Model Context Protocol</h2>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
          <RefreshCw className="mr-1 size-3" aria-hidden />
          Reload
        </Button>
      </header>

      {error ? (
        <Card>
          <CardBody className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-block-ink" aria-hidden />
            <p className="text-sm text-foreground">{error}</p>
          </CardBody>
        </Card>
      ) : null}

      {data ? (
        <>
          <Connections
            data={data}
            busy={busy}
            probe={probe}
            onCreate={onCreate}
            onUpdate={onUpdate}
            onDelete={onDelete}
            onTest={onTest}
          />
          <ToolGovernance data={data} busy={busy} onWrite={onWrite} />
          <AegisMcpPanel endpoint={data.selfEndpoint} token={token} />
        </>
      ) : null}
    </div>
  )
}

/** Section mount for `mcp` — platform admin only, enforced by the server. */
export function McpConsoleMount(): ReactElement {
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-md border border-dashed border-border bg-surface-2 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
      <McpConsoleBody token={session?.token ?? null} />
    </BackendGate>
  )
}
