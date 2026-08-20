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
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/primitives/button'
import { Card, CardBody } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
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
  type PatchSummary,
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
  outdated: 'text-block-ink',
  unknown: 'text-muted-foreground',
}

const POSTURE_VARIANT: Record<PatchPosture, 'risk' | 'ok' | 'neutral'> = {
  'action-needed': 'risk',
  current: 'ok',
  unverified: 'neutral',
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

  if (load.status === 'idle' || load.status === 'loading') {
    return (
      <Card>
        <CardBody>
          <LoadingState rows={5} label="Checking package freshness…" />
        </CardBody>
      </Card>
    )
  }

  if (load.status === 'error') {
    return (
      <Card>
        <CardBody>
          <ErrorState
            error={load.message}
            fallback="The patch check could not be run."
            retry={() => run()}
          />
        </CardBody>
      </Card>
    )
  }

  if (summary == null || posture == null) return <span />

  const online = load.data.online

  return (
    <div className="flex flex-col gap-4">
      {/* The verdict, and the freshness split that reconciles it. */}
      <Card>
        <CardBody className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-3">
            <Badge tone={POSTURE_VARIANT[posture]} className="gap-1.5">
              {posture === 'action-needed' ? (
                <AlertTriangle className="size-3 shrink-0" aria-hidden />
              ) : posture === 'current' ? (
                <CheckCircle2 className="size-3 shrink-0" aria-hidden />
              ) : (
                <WifiOff className="size-3 shrink-0" aria-hidden />
              )}
              {POSTURE_LABEL[posture]}
            </Badge>
            <span
              className={cn(
                'inline-flex items-center gap-1.5 text-[0.8125rem]',
                online ? 'text-ok-ink' : 'text-risk-ink',
              )}
            >
              {online ? (
                <CheckCircle2 className="size-3.5 shrink-0" aria-hidden />
              ) : (
                <WifiOff className="size-3.5 shrink-0" aria-hidden />
              )}
              {online ? 'Verified against the registry' : 'Registry unreachable — nothing below is confirmed'}
            </span>
            <InfoTip label="Why an offline check is never “current”">
              Outdated dependencies are the most common source of known-CVE exposure, so this
              compares each installed pin against the latest published release. A patch claim
              nobody could verify is worse than none — when the registry does not answer, the
              posture stays “unverified” and never resolves to up to date.{' '}
              {load.data.note}
            </InfoTip>
            <Button
              size="sm"
              className="ml-auto"
              onClick={() => run()}
              disabled={load.status !== 'ready'}
            >
              <RefreshCw className="size-3.5" aria-hidden />
              Re-check
            </Button>
          </div>

          <FreshnessBar summary={summary} online={online} />
        </CardBody>
      </Card>

      <DataPanel
        eyebrow="installed vs latest"
        title="Every pin, worst first"
        maxHeight={560}
        toolbar={
          <label className="flex w-full items-center gap-2 rounded-md border border-border bg-surface-2/40 px-2.5 py-1.5 text-sm focus-within:ring-2 focus-within:ring-ring sm:max-w-xs">
            <Search className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter packages…"
              aria-label="Filter packages by name"
              className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
            />
          </label>
        }
        actions={
          <Badge tone="neutral" className="gap-1.5">
            <ShieldCheck className="size-3 shrink-0" aria-hidden />
            <Figure>{rows.length}</Figure>
          </Badge>
        }
        footer={
          <Receipt
            label="Checked"
            origin={new Date(load.data.checked_at).toLocaleString()}
            detail={online ? 'against the package registry' : 'registry unreachable — statuses are unverified'}
            className="w-full border-t-0 pt-0"
          />
        }
      >
        <Table className="min-w-[620px]">
          <THead>
            <TH className="text-left">Package</TH>
            <TH className="text-left">Installed</TH>
            <TH className="text-left">Latest</TH>
            <TH className="text-left">Status</TH>
            <TH className="text-left">Note</TH>
          </THead>
          <TBody>
            {rows.map((r) => (
              <PatchRow key={r.name} result={r} />
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-sm text-muted-foreground">
                  No packages match &ldquo;{filter.trim()}&rdquo;.
                </td>
              </tr>
            )}
          </TBody>
        </Table>
      </DataPanel>
    </div>
  )
}

/**
 * The freshness split — one bar, three ordered bands, drawn from the same counts
 * the table below reconciles against.
 *
 * The screen used to state those counts as three chips, and a reader had to do
 * the division themselves to know whether "4 outdated" was a rounding error or a
 * third of the stack. Status hues carry it here because this *is* the reserved
 * status set (DESIGN.md §2), and every band ships with its icon and its word.
 * When the registry did not answer, the whole bar goes neutral: an unverified
 * check has no green to give.
 */
function FreshnessBar({
  summary,
  online,
}: {
  summary: PatchSummary
  online: boolean
}): ReactElement {
  const bands = [
    { key: 'outdated', label: 'update available', count: summary.outdated, fill: 'bg-block', ink: 'text-block-ink', Icon: AlertTriangle },
    { key: 'unknown', label: 'unverified', count: summary.unknown, fill: 'bg-surface-2', ink: 'text-muted-foreground', Icon: WifiOff },
    { key: 'current', label: 'current', count: summary.current, fill: 'bg-ok', ink: 'text-ok-ink', Icon: CheckCircle2 },
  ] as const
  const total = Math.max(1, summary.total)
  return (
    <div className="flex flex-col gap-3">
      <div
        className="flex h-4 w-full gap-0.5 overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={bands
          .map((b) => `${b.count} of ${summary.total} ${b.label}`)
          .join('; ')}
      >
        {bands.map((b) =>
          b.count === 0 ? null : (
            <span
              key={b.key}
              className={cn(
                'h-full first:rounded-l-full last:rounded-r-full',
                // An unverified check has no verified band to colour.
                online || b.key === 'outdated' ? b.fill : 'bg-surface-2 ring-1 ring-inset ring-border',
              )}
              style={{ width: `${(b.count / total) * 100}%` }}
            />
          ),
        )}
      </div>
      <ul className="flex flex-wrap items-center gap-x-5 gap-y-2">
        {bands.map((b) => (
          <li key={b.key} className="flex items-center gap-1.5">
            <b.Icon className={cn('size-3.5 shrink-0', b.ink)} aria-hidden />
            <Figure size="stat" className="text-foreground">
              {b.count}
            </Figure>
            <span className="text-[0.8125rem] text-muted-foreground">{b.label}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** One package row; outdated rows carry a subtle warning wash. */
function PatchRow({ result }: { result: PatchResult }): ReactElement {
  return (
    <TR className={cn(result.status === 'outdated' && 'bg-block/5')}>
      <TD className="font-medium">{result.name}</TD>
      <TD className="whitespace-nowrap">
        {result.installed === null ? (
          <NotPublished what="not installed" />
        ) : (
          <Figure className="text-muted-foreground">{result.installed}</Figure>
        )}
      </TD>
      <TD className="whitespace-nowrap">
        {result.latest === null ? (
          <NotPublished what="registry did not answer" />
        ) : (
          <Figure className="text-muted-foreground">{result.latest}</Figure>
        )}
      </TD>
      <TD className="whitespace-nowrap">
        <span
          className={cn(
            'inline-flex items-center gap-1.5 text-[0.8125rem] font-medium',
            STATUS_TONE[result.status],
          )}
        >
          {result.status === 'outdated' ? (
            <AlertTriangle className="size-3 shrink-0" aria-hidden />
          ) : result.status === 'current' ? (
            <CheckCircle2 className="size-3 shrink-0" aria-hidden />
          ) : (
            <WifiOff className="size-3 shrink-0" aria-hidden />
          )}
          {PATCH_STATUS_LABEL[result.status]}
        </span>
      </TD>
      <TD className="text-[0.8125rem] text-muted-foreground">
        {result.note ?? <NotPublished what="no note" />}
      </TD>
    </TR>
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
      <div className="space-y-4">
        <PageHeader eyebrow="installed vs latest" title="Patch check" />
        <PatchCheck token={session?.token ?? null} />
      </div>
    </BackendGate>
  )
}
