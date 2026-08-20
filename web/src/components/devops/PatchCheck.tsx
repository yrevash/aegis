'use client'

import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  WifiOff,
} from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { checkPatches } from '@/lib/api/client'
import { Badge } from '@/components/primitives/badge'
import { Button } from '@/components/primitives/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { PatchCheckResponse, PatchResult } from '@/lib/api/types'

import {
  PATCH_STATUS_LABEL,
  POSTURE_LABEL,
  filterByName,
  patchPosture,
  sortByStatus,
  summarizePatches,
  type PatchPosture,
  type PatchStatus,
} from './stackDisplay'

/**
 * DevOps — Patch Check.
 *
 * Compares each installed pin against the latest published release. The honesty
 * rule here is load-bearing: a patch claim you cannot verify is worse than none,
 * so when the registry is unreachable (`online: false`) the banner says so
 * plainly and the posture never resolves to "up to date" — an offline check is
 * always shown as unverified.
 *
 * Counting / ordering / the offline posture rule live in the recharts-free
 * `stackDisplay` module (unit-tested); this file fetches and renders.
 */

type LoadState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: PatchCheckResponse }

const STATUS_TONE: Record<PatchStatus, string> = {
  current: 'text-ok-ink',
  outdated: 'text-risk-ink',
  unknown: 'text-muted-foreground',
}

const POSTURE_VARIANT: Record<PatchPosture, 'risk' | 'ok' | 'secondary'> = {
  'action-needed': 'risk',
  current: 'ok',
  unverified: 'secondary',
}

export function PatchCheck({ token }: { token: string | null }): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'idle' })
  const [filter, setFilter] = useState('')

  const run = useCallback(() => {
    let alive = true
    setLoad({ status: 'loading' })
    checkPatches(undefined, token)
      .then((data) => alive && setLoad({ status: 'ready', data }))
      .catch((e: unknown) =>
        alive &&
        setLoad({ status: 'error', message: e instanceof Error ? e.message : 'Patch check failed' }),
      )
    return () => {
      alive = false
    }
  }, [token])

  useEffect(() => run(), [run])

  const summary = load.status === 'ready' ? summarizePatches(load.data.results) : null
  const posture =
    load.status === 'ready' && summary ? patchPosture(summary, load.data.online) : null
  const rows =
    load.status === 'ready' ? sortByStatus(filterByName(load.data.results, filter)) : []

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0">
        <ShieldCheck className="size-4 text-ok-ink" aria-hidden />
        <CardTitle>Installed pins against the registry</CardTitle>
        {posture && <Badge variant={POSTURE_VARIANT[posture]}>{POSTURE_LABEL[posture]}</Badge>}
        <InfoTip label="Why this matters">
          Why this matters: outdated dependencies are the most common source of known-CVE exposure.
          This compares each installed pin against the latest release — and a patch claim you
          can&rsquo;t verify is worse than none, so an offline check is shown as unverified, never
          &ldquo;current&rdquo;.
        </InfoTip>
        <Button
          size="sm"
          className="ml-auto"
          onClick={() => run()}
          disabled={load.status === 'loading'}
        >
          {load.status === 'loading' ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RefreshCw className="size-3.5" aria-hidden />
          )}
          {load.status === 'ready' ? 'Re-check' : 'Check for patches'}
        </Button>
      </CardHeader>
      <CardContent>
        {(load.status === 'idle' || load.status === 'loading') && (
          <LoadingState rows={5} label="Checking package freshness…" />
        )}

        {load.status === 'error' && (
          <ErrorState
            error={load.message}
            fallback="The patch check could not be run."
            retry={() => run()}
          />
        )}

        {load.status === 'ready' && summary && (
          <div className="flex flex-col gap-4">
            {/* Honest online / offline banner */}
            {load.data.online ? (
              <div className="flex items-start gap-2 rounded-md border border-ok/50 bg-ok/10 p-2.5 text-[0.78rem] text-ok-ink">
                <CheckCircle2 className="mt-px size-4 shrink-0" aria-hidden />
                <span>Verified against the package registry. {load.data.note}</span>
              </div>
            ) : (
              <div className="flex items-start gap-2 rounded-md border border-risk/50 bg-risk/10 p-2.5 text-[0.78rem] text-risk-ink">
                <WifiOff className="mt-px size-4 shrink-0" aria-hidden />
                <span>
                  <strong className="font-semibold">Offline — patch status could not be verified</strong>{' '}
                  against the registry. Do not read the rows below as confirmation that anything is up
                  to date. {load.data.note}
                </span>
              </div>
            )}

            {/* Summary strip */}
            <div className="flex flex-wrap items-center gap-2">
              <Chip label="outdated" count={summary.outdated} tone="risk" icon={AlertTriangle} />
              <Chip label="current" count={summary.current} tone="ok" icon={CheckCircle2} />
              <Chip label="unverified" count={summary.unknown} tone="muted" />
              <span className="ml-auto font-mono text-[0.68rem] text-muted-foreground">
                checked {new Date(load.data.checked_at).toLocaleString()}
              </span>
            </div>

            {/* Package filter */}
            <label className="flex items-center gap-2 rounded-md border border-border/70 bg-surface/40 px-2.5 py-1.5 text-sm focus-within:ring-2 focus-within:ring-ring">
              <Search className="size-3.5 text-muted-foreground" aria-hidden />
              <input
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter packages…"
                aria-label="Filter packages by name"
                className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
              />
            </label>

            <div className="overflow-x-auto rounded-lg border border-border/60">
              <table className="w-full min-w-[600px] text-sm">
                <thead>
                  <tr className="border-b border-border/60 bg-surface-2/40 text-left">
                    <th className="eyebrow px-3 py-2 font-normal">Package</th>
                    <th className="eyebrow px-3 py-2 font-normal">Installed</th>
                    <th className="eyebrow px-3 py-2 font-normal">Latest</th>
                    <th className="eyebrow px-3 py-2 font-normal">Status</th>
                    <th className="eyebrow px-3 py-2 font-normal">Note</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <PatchRow key={r.name} result={r} />
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-3 py-6 text-center text-sm text-muted-foreground">
                        No packages match &ldquo;{filter.trim()}&rdquo;.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/** One package row; outdated rows carry a subtle warning wash. */
function PatchRow({ result }: { result: PatchResult }): ReactElement {
  return (
    <tr
      className={cn(
        'border-b border-border/40 last:border-0',
        result.status === 'outdated' && 'bg-risk/5',
      )}
    >
      <td className="px-3 py-2 font-medium text-foreground">{result.name}</td>
      <td className="px-3 py-2 text-[0.72rem] text-muted-foreground">
        {result.installed === null ? <NotPublished what="not installed" /> : <Figure>{result.installed}</Figure>}
      </td>
      <td className="px-3 py-2 text-[0.72rem] text-muted-foreground">
        {result.latest === null ? <NotPublished what="registry did not answer" /> : <Figure>{result.latest}</Figure>}
      </td>
      <td className={cn('px-3 py-2 text-[0.8125rem] font-medium', STATUS_TONE[result.status])}>
        {PATCH_STATUS_LABEL[result.status]}
      </td>
      <td className="px-3 py-2 text-[0.8125rem] text-muted-foreground">
        {result.note ?? <NotPublished what="no note" />}
      </td>
    </tr>
  )
}

/**
 * A version the check could not establish.
 *
 * It was an em dash, which on this table is actively misleading: an unreachable
 * registry and an uninstalled package produce the same blank, and the whole point
 * of the page is that a patch claim you cannot verify is worse than none.
 */
function NotPublished({ what }: { what: string }): ReactElement {
  return <span className="text-xs text-muted-foreground italic">{what}</span>
}

/** One count chip in the summary strip. */
function Chip({
  label,
  count,
  tone,
  icon: Icon,
}: {
  label: string
  count: number
  tone: 'risk' | 'ok' | 'muted'
  icon?: typeof AlertTriangle
}): ReactElement {
  const toneClass =
    tone === 'risk'
      ? 'border-risk/50 bg-risk/10 text-risk-ink'
      : tone === 'ok'
        ? 'border-ok/50 bg-ok/10 text-ok-ink'
        : 'border-border/70 bg-surface-2/50 text-muted-foreground'
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-[0.72rem]',
        toneClass,
      )}
    >
      {Icon && count > 0 && <Icon className="size-3" aria-hidden />}
      <Figure className="font-semibold">{count}</Figure>
      {label}
    </span>
  )
}

/** Client entry for the Patch Check section — gated on a reachable backend. */
export function PatchMount(): ReactElement {
  // Hand the child the real session bearer, and hold it back until the persisted
  // session has been restored — mounting with a constant `null` would fetch with
  // no `Authorization` header and never retry.
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface-2/40 p-4">
        <LoadingState rows={4} label="Restoring the session…" />
      </div>
    )
  }

  return (
    <BackendGate>
      <TooltipProvider>
        <div className="space-y-4">
          <SectionHeader
            as="h1"
            eyebrow="installed vs latest"
            title="Patch check"
            note="Each installed pin against the latest published release. An offline check never resolves to “up to date” — a patch claim nobody could verify is worse than none."
          />
          <PatchCheck token={session?.token ?? null} />
        </div>
      </TooltipProvider>
    </BackendGate>
  )
}
