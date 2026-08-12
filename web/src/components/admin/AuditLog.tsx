'use client'

import { CheckCircle2, Copy, Download, ListChecks, Loader2, ScrollText, Search, ShieldAlert, WifiOff } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement, type ReactNode } from 'react'

import { getAudit } from '@/lib/api/client'
import { BarChart } from '@/components/charts/BarChart'
import { CountUp } from '@/components/shared'
import { Badge } from '@/components/primitives/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { SIGNALS, type Signal } from '@/config/signals'
import { useAuth } from '@/lib/auth/AuthContext'
import { probeBackend, type ResolvedMode } from '@/lib/api/mode'
import { cn } from '@/lib/utils'
import type { AuditLogRow } from '@/lib/api/types'

import {
  actorsOf,
  auditCounts,
  eventsPerHour,
  filterRows,
  modelsOf,
  resultOf,
  rowsToCsv,
  type AuditResult,
} from './audit'

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
}

/**
 * Aegis Trace — the append-only audit trail. Every recorded action with its
 * actor, model, trace id and approver, led by a pulse header (events, blocks,
 * approvals + a per-hour shape) so a reviewer sees the activity before the rows.
 * Backed by `GET /audit`; the rows are the real Postgres trail, not fixtures.
 */
export function AuditLog({ token }: AuditLogProps): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [result, setResult] = useState<AuditResult | null>(null)
  const [actor, setActor] = useState<string | null>(null)
  const [model, setModel] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  useEffect(() => {
    let alive = true
    setLoad({ status: 'loading' })
    getAudit(token)
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
  }, [token])

  const rows = load.status === 'ready' ? load.rows : NO_ROWS
  const counts = useMemo(() => auditCounts(rows), [rows])
  const perHour = useMemo(() => eventsPerHour(rows, 12), [rows])
  const actors = useMemo(() => actorsOf(rows), [rows])
  const models = useMemo(() => modelsOf(rows), [rows])
  const filtered = useMemo(
    () => filterRows(rows, { result, actor, model, search }),
    [rows, result, actor, model, search],
  )

  /** Download the currently-filtered rows as a CSV file. */
  const exportCsv = (): void => {
    const blob = new Blob([rowsToCsv(filtered)], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `aegis-audit-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
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

          {load.status === 'ready' && rows.length > 0 && (
            <div className="ml-auto flex flex-wrap items-center gap-1.5">
              <div className="relative">
                <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search actions…"
                  aria-label="Search the audit trail"
                  className="h-7 w-44 rounded-md border border-border bg-card pr-2 pl-7 text-[0.72rem] text-foreground placeholder:text-muted-foreground/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </div>
              <FilterChip active={result === null} onClick={() => setResult(null)}>
                All
              </FilterChip>
              <FilterChip active={result === 'completed'} onClick={() => setResult('completed')}>
                Completed
              </FilterChip>
              <FilterChip active={result === 'blocked'} onClick={() => setResult('blocked')}>
                Blocked
              </FilterChip>
              {actors.length > 0 && (
                <select
                  value={actor ?? ''}
                  onChange={(e) => setActor(e.target.value === '' ? null : e.target.value)}
                  className="h-7 rounded-md border border-border bg-card px-2 font-mono text-[0.68rem] text-muted-foreground"
                  aria-label="Filter by actor"
                >
                  <option value="">All actors</option>
                  {actors.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              )}
              {models.length > 0 && (
                <select
                  value={model ?? ''}
                  onChange={(e) => setModel(e.target.value === '' ? null : e.target.value)}
                  className="h-7 max-w-[10rem] rounded-md border border-border bg-card px-2 font-mono text-[0.68rem] text-muted-foreground"
                  aria-label="Filter by model"
                >
                  <option value="">All models</option>
                  {models.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              )}
              <button
                type="button"
                onClick={exportCsv}
                title="Export the filtered rows as CSV"
                className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-card px-2 font-mono text-[0.68rem] text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
              >
                <Download className="size-3.5" /> CSV
              </button>
            </div>
          )}
        </CardHeader>

        <CardContent>
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
              <p className="text-sm font-medium text-foreground">Nothing audited yet</p>
              <p className="max-w-xs text-xs text-muted-foreground">
                Recorded actions appear here, newest first — each with its actor, model and trace.
              </p>
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
                  {filtered.map((r, i) => (
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
                        <ResultDot result={resultOf(r.action)} />
                      </td>
                    </tr>
                  ))}
                  {filtered.length === 0 && (
                    <tr>
                      <td colSpan={COLUMNS.length} className="py-8 text-center text-sm text-muted-foreground">
                        No rows match this filter.
                      </td>
                    </tr>
                  )}
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
function ResultDot({ result }: { result: AuditResult }): ReactElement {
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

/** A small filter pill for the result / actor scope. */
function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}): ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-md border px-2 py-0.5 font-mono text-[0.68rem] transition-colors',
        active
          ? 'border-primary bg-primary text-primary-foreground'
          : 'border-border bg-card text-muted-foreground hover:bg-surface-2',
      )}
    >
      {children}
    </button>
  )
}

/**
 * Client entry for the Audit section (admin + devops). Runs the boot probe once
 * (live-first, mock fallback), shows the honest offline banner, then renders the
 * trail wired to `GET /audit`.
 */
export function AuditMount(): ReactElement {
  // `GET /audit` is RBAC-scoped: hand the child the real session bearer, and hold
  // it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()
  const [mode, setMode] = useState<ResolvedMode | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setMode(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (mode === null || !hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <TooltipProvider>
      <div className="space-y-4">
        <div>
          <p className="eyebrow mb-1">Postgres audit</p>
          <h1 className="t-hero text-foreground">Audit</h1>
        </div>
        {mode.mode === 'mock' && (
          <div
            role="status"
            className="flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
          >
            <WifiOff className="size-3.5 shrink-0" />
            <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
          </div>
        )}
        <AuditLog token={session?.token ?? null} />
      </div>
    </TooltipProvider>
  )
}
