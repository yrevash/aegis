'use client'

import { CheckCircle2, Copy, Download, ListChecks, Loader2, ScrollText, ShieldAlert } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { getAudit, getTenants } from '@/lib/api/client'
import { apiMessage, statusOf } from '@/lib/api/apiError'
import { startReportDownload } from '@/lib/api/reports'
import { BarChart } from '@/components/charts/BarChart'
import { AuditFilterBar } from '@/components/audit/AuditFilterBar'
import {
  auditQueryString,
  emptyStateFor,
  exportFilters,
  unexportableFilters,
  EMPTY_AUDIT_QUERY,
  type AuditQuery,
} from '@/components/audit/query'
import { CountUp } from '@/components/shared'
import { Badge } from '@/components/primitives/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { SIGNALS, type Signal } from '@/config/signals'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { AuditLogRow, AuditOutcome, Tenant } from '@/lib/api/types'

import { auditCounts, eventsPerHour } from './audit'

/** Load state for the audit fetch. */
type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: AuditLogRow[] }

const COLUMNS = ['Time', 'Action', 'Actor', 'Model', 'Trace', 'Approved by', 'Result'] as const

/** Stable empty reference so memo deps don't churn before rows load. */
const NO_ROWS: AuditLogRow[] = []

/** Local wall-clock time from an ISO 8601 timestamp (projector-legible). */
function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

interface AuditLogProps {
  token: string | null
  /** Tenants to offer in the selector — platform admin only; empty for everyone else. */
  tenants?: Tenant[]
}

/**
 * Aegis Trace — the append-only audit trail. Every recorded action with its
 * actor, model, trace id and approver, led by a pulse header (events, blocks,
 * approvals + a per-hour shape) so a reviewer sees the activity before the rows.
 * Backed by `GET /audit`; the rows are the real Postgres trail, not fixtures.
 *
 * **The filters are the server's** (§7.11). Changing one re-runs the query rather than
 * hiding rows already on screen, so the header figures always describe the same set the
 * table shows and a search reaches the whole trail rather than the last page of it.
 */
export function AuditLog({ token, tenants = [] }: AuditLogProps): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [query, setQuery] = useState<AuditQuery>(EMPTY_AUDIT_QUERY)
  const [exportError, setExportError] = useState<string | null>(null)
  const search = useMemo(() => auditQueryString(query), [query])

  useEffect(() => {
    let alive = true
    setLoad({ status: 'loading' })
    getAudit(token, search)
      .then((res) => {
        if (alive) setLoad({ status: 'ready', rows: res.rows })
      })
      .catch((error: unknown) => {
        if (alive) {
          setLoad({
            status: 'error',
            message: error instanceof Error ? error.message : 'Failed to load audit trail',
          })
        }
      })
    return () => {
      alive = false
    }
  }, [token, search])

  const rows = load.status === 'ready' ? load.rows : NO_ROWS
  const counts = useMemo(() => auditCounts(rows), [rows])
  const perHour = useMemo(() => eventsPerHour(rows, 12), [rows])
  const empty = useMemo(() => emptyStateFor(query), [query])

  const dropped = useMemo(() => unexportableFilters(query), [query])

  /**
   * Hand the export to `GET /reports/audit.csv` (§7.12).
   *
   * It used to serialise `rows` into a blob — the rows *on screen*, capped by the page
   * limit, in a file that said nothing about its own scope or window. That file looked
   * like a complete export of a filtered query and was not. The server's export is
   * streamed with no limit, scoped through the sealed `TenantScope`, audited as
   * `report.export` before the first byte, and opens with a preamble naming the scope,
   * window, source and filters — so the file states what it is.
   */
  const exportCsv = (): void => {
    setExportError(null)
    startReportDownload(token, 'audit', exportFilters(query)).catch((error: unknown) => {
      const status = statusOf(error)
      setExportError(
        status === null
          ? 'The export could not be started. Check the backend is reachable, then retry.'
          : apiMessage(status),
      )
    })
  }

  return (
    <div className="flex flex-col gap-4">
      <Card className="gap-0 p-0">
        <div className="grid grid-cols-1 gap-4 p-5 lg:grid-cols-[1fr_1fr_1fr_1.6fr] lg:divide-x lg:divide-border/70">
          <HeaderStat label="Events" icon={ListChecks} signal="graph" value={counts.total} sub="recorded" />
          <HeaderStat label="Blocked" icon={ShieldAlert} signal="block" value={counts.blocked} sub="guardrail / denied" className="lg:pl-5" />
          <HeaderStat label="Approved" icon={CheckCircle2} signal="ok" value={counts.approved} sub="human-gated" className="lg:pl-5" />
          <div className="lg:pl-5">
            <div className="mb-1 flex items-center gap-2">
              <span className="eyebrow">Activity</span>
              <span className="font-mono text-[0.62rem] text-muted-foreground">last 12h</span>
            </div>
            {load.status === 'ready' ? (
              <BarChart data={perHour} index="hour" category="count" color="graph" height={72} />
            ) : (
              <div className="h-[72px]" />
            )}
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0">
          <ScrollText className="size-4 text-agent" />
          <CardTitle>Audit trail</CardTitle>
          <div className="ml-1 flex items-center gap-1">
            <Badge variant="secondary">append-only</Badge>
            <InfoTip label="What append-only means">
              Rows are only ever added, never edited or deleted — a tamper-evident record. This is a
              real, load-bearing property of the trail.
            </InfoTip>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={exportCsv}
              title="Download the whole filtered trail as CSV"
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-card px-2.5 font-mono text-[0.7rem] text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              <Download aria-hidden className="size-3.5" /> CSV
            </button>
            <InfoTip label="What the CSV contains">
              The whole filtered trail, not the page on screen — streamed from the server with
              no row limit, scoped to what you may read, and opening with a preamble naming the
              scope, window and filters it was built from. The download is itself recorded as a
              report.export row.
            </InfoTip>
          </div>
        </CardHeader>

        <CardContent className="flex flex-col gap-4">
          {exportError !== null && (
            <p role="alert" className="text-sm text-destructive">
              {exportError}
            </p>
          )}

          {/* The export takes the actor, action prefix and time range; the rest of this
              form has no counterpart on the route. Saying so is the point: a file that
              quietly holds more than the table it came from is evidence of the wrong
              thing. */}
          {dropped.length > 0 && (
            <p className="rounded-md border border-border bg-surface-2/60 px-3 py-2 text-xs text-muted-foreground">
              The CSV carries the actor, action prefix and time range. It cannot narrow by{' '}
              <span className="text-foreground">{dropped.join(', ')}</span>, so it will hold more
              rows than the table below. Clear those filters before exporting if the file must
              match what you see.
            </p>
          )}

          <AuditFilterBar
            value={query}
            onChange={setQuery}
            tenants={tenants}
            busy={load.status === 'loading'}
          />

          {load.status === 'loading' && (
            <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading audit trail…
            </div>
          )}

          {load.status === 'error' && (
            <div className="py-10 text-sm text-destructive">
              Could not load the audit trail. {load.message}
            </div>
          )}

          {load.status === 'ready' && rows.length === 0 && (
            <div className="flex flex-col items-center gap-2 py-14 text-center">
              <span className="grid size-10 place-items-center rounded-full bg-surface-2">
                <ScrollText className="size-5 text-muted-foreground" />
              </span>
              <p className="text-sm font-medium text-foreground">{empty.title}</p>
              <p className="max-w-sm text-xs text-muted-foreground">{empty.hint}</p>
            </div>
          )}

          {load.status === 'ready' && rows.length > 0 && (
            <div className="max-h-[520px] overflow-auto">
              <table className="w-full min-w-[900px] text-sm [&_td]:pr-8 [&_td:last-child]:pr-0 [&_th]:pr-8 [&_th:last-child]:pr-0">
                <thead className="sticky top-0 z-10 bg-card">
                  <tr className="border-b border-border/70 text-left">
                    {COLUMNS.map((h) => (
                      <th key={h} className="eyebrow pb-2 font-normal whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr
                      key={r.id}
                      className="animate-trace-in border-b border-border/40 align-top transition-colors last:border-0 hover:bg-surface-2/50"
                      style={{ animationDelay: `${Math.min(i, 14) * 28}ms` }}
                    >
                      <td className="tabular py-2.5 font-mono text-[0.72rem] whitespace-nowrap text-muted-foreground">
                        {formatTime(r.ts)}
                      </td>
                      <td className="min-w-[16rem] py-2.5 font-medium text-foreground">{r.action}</td>
                      <td className="py-2.5 font-mono text-[0.72rem] whitespace-nowrap text-muted-foreground">
                        {r.actor ?? '—'}
                      </td>
                      <td className="py-2.5 font-mono text-[0.72rem] whitespace-nowrap text-agent-ink">
                        {r.model ?? '—'}
                      </td>
                      <td className="py-2.5 whitespace-nowrap">
                        <TraceChip traceId={r.trace_id} />
                      </td>
                      <td className="py-2.5 font-mono text-[0.72rem] whitespace-nowrap text-muted-foreground">
                        {r.approved_by ?? '—'}
                      </td>
                      <td className="py-2.5 whitespace-nowrap">
                        <ResultDot result={r.outcome} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

/** One header figure: icon chip, count-up value, short sub-line. */
function HeaderStat({
  label,
  icon: Icon,
  signal,
  value,
  sub,
  className,
}: {
  label: string
  icon: typeof ListChecks
  signal: Signal
  value: number
  sub: string
  className?: string
}): ReactElement {
  const token = SIGNALS[signal]
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <div className="flex items-center gap-2">
        <span className={cn('grid size-6 place-items-center rounded-md', token.bg)}>
          <Icon className={cn('size-3.5', token.text)} />
        </span>
        <span className="eyebrow">{label}</span>
      </div>
      <CountUp value={value} className="t-metric text-foreground" />
      <span className="font-mono text-[0.68rem] text-muted-foreground">{sub}</span>
    </div>
  )
}

/** Result marker — a coloured dot paired with its word (never colour alone). */
function ResultDot({ result }: { result: AuditOutcome }): ReactElement {
  const ok = result === 'completed'
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className="inline-block size-2 shrink-0 rounded-full"
        style={{ background: ok ? 'var(--ok-ink)' : 'var(--block-ink)' }}
      />
      <span className={cn('text-[0.8125rem]', ok ? 'text-foreground' : 'text-block-ink')}>{result}</span>
    </span>
  )
}

/** A mono trace id with a copy affordance; em dash when absent. */
function TraceChip({ traceId }: { traceId: string | null }): ReactElement {
  if (traceId == null) {
    return <span className="font-mono text-[0.72rem] text-muted-foreground">—</span>
  }
  const copy = (): void => {
    void navigator.clipboard?.writeText(traceId)
  }
  return (
    <button
      type="button"
      onClick={copy}
      className="group inline-flex items-center gap-1 rounded-md border border-border/70 bg-surface-2/60 px-1.5 py-0.5 font-mono text-[0.68rem] text-graph-ink transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      title="Copy trace id"
    >
      {traceId}
      <Copy aria-hidden className="size-3 text-muted-foreground/60 group-hover:text-muted-foreground" />
    </button>
  )
}

/** Client entry for the Audit section — gated on a reachable backend. */
export function AuditMount(): ReactElement {
  // `GET /audit` is RBAC-scoped: hand the child the real session bearer, and hold
  // it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()
  const [tenants, setTenants] = useState<Tenant[]>([])

  // The tenant selector renders for a platform admin and nobody else (§7.11). This is a
  // convenience, not the control: `_scope_tenant` refuses a cross-tenant `tenant_id`
  // server-side whoever asks, so an empty list here removes a picker rather than a
  // permission. A failed roster read leaves the list empty — no picker beats a picker
  // whose options are a guess.
  const isPlatformAdmin = hydrated && session?.fineRole === 'platform_admin'
  const token = session?.token ?? null
  useEffect(() => {
    if (!isPlatformAdmin) {
      setTenants([])
      return
    }
    let alive = true
    getTenants(token)
      .then((res) => {
        if (alive) setTenants(res.rows)
      })
      .catch(() => {
        if (alive) setTenants([])
      })
    return () => {
      alive = false
    }
  }, [isPlatformAdmin, token])

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
            <p className="eyebrow mb-1">Postgres audit</p>
            <h1 className="t-hero text-foreground">Audit</h1>
          </div>
          <AuditLog token={token} tenants={tenants} />
        </div>
      </TooltipProvider>
    </BackendGate>
  )
}
