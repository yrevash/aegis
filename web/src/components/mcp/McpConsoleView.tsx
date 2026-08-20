'use client'

import { RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Button } from '@/components/primitives/button'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { errorSentence } from '@/lib/api/apiError'
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
import { McpPosture } from './McpPosture'
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
        setError(errorSentence(caught, 'That change did not reach the registry. Try it again.'))
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
      setError(errorSentence(caught, 'The MCP registry did not load. Check the backend is up, then retry.'))
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
    return <LoadingState rows={5} label="Reading the MCP registry…" />
  }

  return (
    <TooltipProvider>
    <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-4">
      <PageHeader
        eyebrow="aegis · mcp"
        title="Model Context Protocol"
        actions={
          <>
            <InfoTip label="What this screen answers">
              Which external servers our agents may reach, what each of their tools may do,
              and what this deployment offers back over the protocol. Nothing on this page
              executes an external tool — that path runs through the agent, behind the human
              gate.
            </InfoTip>
            <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
              <RefreshCw className="mr-1 size-3" aria-hidden />
              Reload
            </Button>
          </>
        }
      />

      {error ? <ErrorState error={error} retry={() => void load()} /> : null}

      {data ? (
        <>
          <McpPosture data={data} />
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
    </TooltipProvider>
  )
}

/** Section mount for `mcp` — platform admin only, enforced by the server. */
export function McpConsoleMount(): ReactElement {
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-dashed border-border bg-surface-2 text-sm text-muted-foreground">
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
