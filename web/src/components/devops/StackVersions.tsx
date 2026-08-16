'use client'

import { Boxes, Layers, Loader2 } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { getStack } from '@/lib/api/client'
import { Badge } from '@/components/primitives/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { StackComponent, StackResponse } from '@/lib/api/types'

import { groupByCategory, summarizeStack, versionLabel } from './stackDisplay'

/**
 * DevOps — Tech Stack & Versions (Aegis SBOM).
 *
 * The ground-truth software bill of materials: every runtime, library and
 * service the agent runs on, grouped by layer, with the Aegis module each
 * component powers. Versions are the real resolved pins — DevOps needs what is
 * actually installed, not a hand-maintained list — and a missing version is
 * shown honestly as "not installed / n-a" rather than papered over.
 *
 * All grouping / counting lives in the recharts-free `stackDisplay` module so it
 * can be unit-tested; this file only fetches and renders.
 */

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: StackResponse }

export function StackVersions({ token }: { token: string | null }): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })

  useEffect(() => {
    let alive = true
    setLoad({ status: 'loading' })
    getStack(token)
      .then((data) => alive && setLoad({ status: 'ready', data }))
      .catch((e: unknown) =>
        alive &&
        setLoad({ status: 'error', message: e instanceof Error ? e.message : 'Failed to load stack' }),
      )
    return () => {
      alive = false
    }
  }, [token])

  const summary = load.status === 'ready' ? summarizeStack(load.data.components) : null
  const groups = load.status === 'ready' ? groupByCategory(load.data.components) : []

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0">
        <Layers className="size-4 text-agent" />
        <CardTitle>Tech Stack &amp; Versions</CardTitle>
        <Badge variant="secondary">SBOM</Badge>
        <InfoTip label="Why this matters">
          Why this matters: DevOps needs the real installed versions, not a hand-maintained list. This
          is a live inventory of every runtime, library and service the agent runs on — so you can
          answer &ldquo;what exactly is in production?&rdquo; and spot unpinned or aged components.
        </InfoTip>
      </CardHeader>
      <CardContent>
        {load.status === 'loading' && (
          <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading stack inventory…
          </div>
        )}

        {load.status === 'error' && (
          <div className="py-10 text-sm text-destructive">Could not load the stack. {load.message}</div>
        )}

        {load.status === 'ready' && load.data.components.length === 0 && (
          <p className="py-10 text-sm text-muted-foreground">No components reported.</p>
        )}

        {load.status === 'ready' && summary && load.data.components.length > 0 && (
          <div className="flex flex-col gap-6">
            {/* Header KPI band */}
            <div className="grid grid-cols-3 gap-3">
              <Stat label="Components" value={summary.total} />
              <Stat
                label="Known versions"
                value={`${summary.withVersion} / ${summary.total}`}
                hint={summary.unknownVersion > 0 ? `${summary.unknownVersion} unresolved` : 'all resolved'}
                tone={summary.unknownVersion > 0 ? 'warn' : 'ok'}
              />
              <Stat label="Layers" value={summary.categories} />
            </div>

            <p className="font-mono text-[0.68rem] text-muted-foreground">
              <Boxes className="mr-1 inline size-3 -translate-y-px" aria-hidden />
              Inventoried {new Date(load.data.generated_at).toLocaleString()}
            </p>

            {groups.map((group) => (
              <section key={group.category}>
                <div className="mb-2 flex items-baseline gap-2">
                  <p className="eyebrow">{group.label}</p>
                  <span className="font-mono text-[0.62rem] text-muted-foreground/70">
                    {group.rows.length}
                  </span>
                </div>
                <div className="overflow-x-auto rounded-lg border border-border/60">
                  <table className="w-full min-w-[560px] text-sm">
                    <thead>
                      <tr className="border-b border-border/60 bg-surface-2/40 text-left">
                        <th className="eyebrow px-3 py-2 font-normal">Component</th>
                        <th className="eyebrow px-3 py-2 font-normal">Package</th>
                        <th className="eyebrow px-3 py-2 font-normal">Version</th>
                        <th className="eyebrow px-3 py-2 font-normal">Powers</th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.rows.map((component) => (
                        <StackRow key={component.package} component={component} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/** One component row: name · package · version badge · Aegis module. */
function StackRow({ component }: { component: StackComponent }): ReactElement {
  const version = versionLabel(component.version)
  return (
    <tr className="border-b border-border/40 last:border-0">
      <td className="px-3 py-2 font-medium text-foreground">{component.name}</td>
      <td className="px-3 py-2 font-mono text-[0.72rem] text-muted-foreground">{component.package}</td>
      <td className="px-3 py-2">
        <span
          className={cn(
            'inline-block rounded border px-1.5 py-0.5 font-mono text-[0.7rem]',
            version.known
              ? 'border-graph/40 bg-graph/10 text-graph-ink'
              : 'border-border/70 bg-surface-2/50 text-muted-foreground italic',
          )}
        >
          {version.text}
        </span>
      </td>
      <td className="px-3 py-2 text-[0.8125rem]">
        {component.aegis_module ? (
          <span className="text-foreground/80">{component.aegis_module}</span>
        ) : (
          <span className="text-muted-foreground/60">shared infra</span>
        )}
      </td>
    </tr>
  )
}

/** One compact figure in the header KPI band. */
function Stat({
  label,
  value,
  hint,
  tone = 'neutral',
}: {
  label: string
  value: string | number
  hint?: string
  tone?: 'neutral' | 'ok' | 'warn'
}): ReactElement {
  return (
    <div className="rounded-lg border border-border/70 bg-surface/40 p-3">
      <p className="eyebrow mb-1">{label}</p>
      <p className="t-metric tabular text-foreground">{value}</p>
      {hint && (
        <p
          className={cn(
            'mt-0.5 font-mono text-[0.62rem]',
            tone === 'warn' ? 'text-risk-ink' : tone === 'ok' ? 'text-ok-ink' : 'text-muted-foreground',
          )}
        >
          {hint}
        </p>
      )}
    </div>
  )
}

/** Client entry for the Tech Stack & Versions section — gated on a reachable backend. */
export function StackMount(): ReactElement {
  // Hand the child the real session bearer, and hold it back until the persisted
  // session has been restored — mounting with a constant `null` would fetch with
  // no `Authorization` header and never retry.
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
      <TooltipProvider>
        <div className="space-y-4">
          <div>
            <p className="eyebrow mb-1">SBOM</p>
            <h1 className="t-hero text-foreground">Tech Stack &amp; Versions</h1>
          </div>
          <StackVersions token={session?.token ?? null} />
        </div>
      </TooltipProvider>
    </BackendGate>
  )
}
