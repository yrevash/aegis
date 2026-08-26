'use client'

import { AlertTriangle, Copy, Download, ScrollText } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { getAudit, getTenants } from '@/lib/api/client'
import { apiMessage, errorSentence, statusOf } from '@/lib/api/apiError'
import { startReportDownload } from '@/lib/api/reports'
import { AuditFilterBar } from '@/components/audit/AuditFilterBar'
import {
  auditQueryString,
  emptyStateFor,
  exportFilters,
  unexportableFilters,
  EMPTY_AUDIT_QUERY,
  type AuditQuery,
} from '@/components/audit/query'
import { Badge } from '@/components/ui/Badge'
import { AuditInsights } from '@/components/audit/AuditInsights'
import { DataPanel } from '@/components/ui/DataPanel'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { AuditLogRow, AuditOutcome, Tenant } from '@/lib/api/types'


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
            message: errorSentence(
              error,
              'The audit trail did not load. Check the backend is reachable, then retry.',
            ),
          })
        }
      })
    return () => {
      alive = false
    }
  }, [token, search])

  const rows = load.status === 'ready' ? load.rows : NO_ROWS
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
      {/*
        The insight layer, which is the whole point of an audit screen. Three
        figures and a 12-hour bar chart said *how much* happened; this says what,
        by whom, and what was refused — six facts, a completed-vs-refused trend on
        a window taken from the data rather than the wall clock, three ranked
        distributions, and the server-side lens chips.

        It lived unmounted for a wave: the component and its tests were finished in
        `components/audit/`, but its mount point is this file, which belonged to a
        different lane. Finished and invisible is not finished.
      */}
      <AuditInsights
        rows={rows}
        loading={load.status === 'loading'}
        query={query}
        onQuery={setQuery}
      />

      {/*
        `DataPanel`, not a hand-rolled `overflow-auto` div. The trail is a 900px-wide
        table, and inside a plain `CardBody` that width became the *page's* width:
        measured at 390px the body ran 231px past the viewport, so the whole document
        slid sideways instead of the table scrolling inside its own box. The panel owns
        a scroll container that cannot widen its card at any width.
      */}
      <DataPanel
        eyebrow="aegis.governance · GET /audit"
        title={
          <span className="flex items-center gap-2">
            <ScrollText className="size-4 shrink-0 text-blue-700" aria-hidden />
            Audit trail
          </span>
        }
        maxHeight={520}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">append-only</Badge>
            <InfoTip label="What append-only means">
              Rows are only ever added, never edited or deleted — a tamper-evident record. This is
              a real, load-bearing property of the trail.
            </InfoTip>
            <button
              type="button"
              onClick={exportCsv}
              title="Download the whole filtered trail as CSV"
              className="inline-flex h-8 touch-manipulation items-center gap-1.5 rounded-md border border-border bg-card px-2.5 font-mono text-[0.7rem] text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
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
        }
        toolbar={
          <div className="w-full space-y-3">
            {exportError !== null && (
              <p role="alert" className="flex items-start gap-2 text-sm text-block-ink">
                <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0" />
                <span>
                  <span className="font-medium">Export refused. </span>
                  {exportError}
                </span>
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
          </div>
        }
        footer={
          <Receipt
            variant="inline"
            origin="GET /audit · Postgres, append-only"
            detail={
              load.status === 'ready'
                ? `${rows.length} of at most ${query.limit} rows matched, filtered server-side`
                : 'filtered server-side, so the figures above describe the same set as the table'
            }
          />
        }
      >
        {load.status === 'loading' && <LoadingState rows={6} label="Reading the audit trail…" />}

        {load.status === 'error' && <ErrorState error={load.message} />}

        {load.status === 'ready' && rows.length === 0 && (
          <EmptyState icon={ScrollText} title={empty.title} body={empty.hint} />
        )}

        {load.status === 'ready' && rows.length > 0 && (
          <table className="w-full min-w-[900px] text-sm [&_td]:pr-8 [&_td:last-child]:pr-0 [&_th]:pr-8 [&_th:last-child]:pr-0">
            <thead className="sticky top-0 z-10 bg-card">
              <tr className="border-b border-border/70 text-left">
                {COLUMNS.map((h) => (
                  <th key={h} scope="col" className="eyebrow pb-2 font-normal whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.id}
                  className="border-b border-border/40 align-top transition-colors last:border-0 hover:bg-surface-2/50"
                >
                  <td className="tabular py-2.5 font-mono text-[0.72rem] whitespace-nowrap text-muted-foreground">
                    {formatTime(r.ts)}
                  </td>
                  <td className="min-w-[16rem] py-2.5 font-medium text-foreground">{r.action}</td>
                  <td className="py-2.5 font-mono text-[0.72rem] whitespace-nowrap text-muted-foreground">
                    {r.actor ?? '—'}
                  </td>
                  <td className="py-2.5 font-mono text-[0.72rem] whitespace-nowrap text-blue-700">
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
        )}
      </DataPanel>
    </div>
  )
}

/** One header figure: icon chip, count-up value, short sub-line. */

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
      // `--blue-700`, not `--blue-600`: this is 11px text on a tinted wash, where
      // blue-600 measures 4.12:1 and fails AA (DESIGN.md §2). The 600 step is a fill,
      // a border and a focus ring — never a small-text step.
      className="group inline-flex touch-manipulation items-center gap-1 rounded-md border border-border/70 bg-surface-2/60 px-1.5 py-0.5 font-mono text-[0.6875rem] text-blue-700 transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      title="Copy trace id"
    >
      {traceId}
      <Copy aria-hidden className="size-3 text-muted-foreground/60 group-hover:text-muted-foreground" />
      <span className="sr-only">Copy trace id</span>
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
      <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
      {/* No `TooltipProvider` here: one is mounted at the root in `auth/Providers`,
          and a second is a second delay budget for the same tooltips. */}
      <div className="space-y-4">
        <PageHeader
          eyebrow="postgres · append-only"
          title="Audit"
          note="Every recorded action with its actor, model, trace id and approver. The filters run on the server, so a search reaches the whole trail rather than the page in view."
        />
        <AuditLog token={token} tenants={tenants} />
      </div>
    </BackendGate>
  )
}
