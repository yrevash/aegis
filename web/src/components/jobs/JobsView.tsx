'use client'

import { CircleSlash, Layers, Loader2, RefreshCw, RotateCcw, XCircle } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Card } from '@/components/primitives/card'
import { BackendGate, BackendUnavailable } from '@/components/shared/BackendGate'
import { UploadPanel } from '@/components/jobs/UploadPanel'
import { cancelJob, getJobs, JobsApiError, requeueJob, type JobRunRow } from '@/lib/api/jobs'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'

/** Load state for the jobs fetch. */
type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: JobRunRow[] }

/** Statuses in which the orchestrator may still be holding a worker slot. */
const IN_FLIGHT = new Set(['pending', 'running', 'reconciling'])

/** Statuses a job can never leave — a re-queue is offered, a cancel is not. */
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled'])

/** The signal colour each job status carries. */
function statusVariant(status: string): 'ok' | 'block' | 'risk' | 'agent' | 'outline' {
  if (status === 'succeeded') return 'ok'
  if (status === 'failed') return 'block'
  if (status === 'reconciling') return 'risk'
  if (status === 'running' || status === 'pending') return 'agent'
  return 'outline'
}

/** Local wall-clock time from an ISO 8601 timestamp, or an em dash. */
function formatTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

interface JobsViewProps {
  token: string | null
}

/**
 * The durable job queue — what background work this tenant has, and the two
 * controls over it.
 *
 * Read from `GET /jobs`, which projects the `job_runs` record layer rather than the
 * orchestrator, so the list still answers when Temporal is down. **Re-queue** goes
 * through admission control: a tenant at its in-flight cap, or without the budget to
 * finish the run, gets a 429 whose reason is rendered here verbatim — backpressure
 * nobody can see is the defect the gate exists to prevent, and a banner that said
 * only "failed" would reproduce it in the browser. **Cancel** signals the
 * orchestrator and records who asked on the row.
 */
export function JobsView({ token }: JobsViewProps): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [busy, setBusy] = useState<number | null>(null)
  const [notice, setNotice] = useState<{ kind: 'ok' | 'refused' | 'error'; text: string } | null>(
    null,
  )

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const res = await getJobs(token)
      setLoad({ status: 'ready', rows: res.rows })
    } catch (error: unknown) {
      setLoad({
        status: 'error',
        message: error instanceof Error ? error.message : 'Failed to load jobs',
      })
    }
  }, [token])

  useEffect(() => {
    void refresh()
  }, [refresh])

  /** Run one row action, surfacing an admission refusal as the reason it carried. */
  const act = async (id: number, action: 'requeue' | 'cancel'): Promise<void> => {
    setBusy(id)
    setNotice(null)
    try {
      const res = action === 'requeue' ? await requeueJob(id, token) : await cancelJob(id, token)
      setNotice({ kind: 'ok', text: res.detail })
      await refresh()
    } catch (error: unknown) {
      if (error instanceof JobsApiError && error.refusedByAdmission) {
        setNotice({
          kind: 'refused',
          text: `Refused by the ${error.gate ?? 'admission'} gate — ${error.message}`,
        })
      } else {
        setNotice({
          kind: 'error',
          text: error instanceof Error ? error.message : 'The action failed',
        })
      }
    } finally {
      setBusy(null)
    }
  }

  if (load.status === 'error') return <BackendUnavailable detail={load.message} />

  const rows = load.status === 'ready' ? load.rows : []
  const inFlight = rows.filter((row) => IN_FLIGHT.has(row.status)).length

  return (
    <div className="flex flex-col gap-4">
      {/* The front door: an upload is what puts a document into this queue at all. */}
      <UploadPanel token={token} onUploaded={() => void refresh()} />

      <Card className="gap-0 p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 p-5">
          <div className="flex items-center gap-3">
            <Layers className="size-5 text-muted-foreground" />
            <div>
              <p className="eyebrow mb-0.5">job_runs · the record layer</p>
              <p className="text-sm text-muted-foreground">
                {load.status === 'loading'
                  ? 'Reading the queue…'
                  : `${rows.length} job${rows.length === 1 ? '' : 's'}, ${inFlight} in flight`}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-surface-2"
          >
            <RefreshCw className="size-3.5" />
            Refresh
          </button>
        </div>
      </Card>

      {notice ? (
        <div
          role="status"
          className={cn(
            'rounded-lg border px-4 py-3 text-sm',
            notice.kind === 'ok' && 'border-ok/60 bg-ok/10 text-ok-ink',
            notice.kind === 'refused' && 'border-risk/60 bg-risk/10 text-risk-ink',
            notice.kind === 'error' && 'border-block/60 bg-block/10 text-block-ink',
          )}
        >
          {notice.text}
        </div>
      ) : null}

      <Card className="gap-0 overflow-hidden p-0">
        {load.status === 'loading' ? (
          <div className="flex min-h-[220px] items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Loading
          </div>
        ) : rows.length === 0 ? (
          <div className="flex min-h-[220px] flex-col items-center justify-center gap-2 px-6 text-center">
            <CircleSlash className="size-7 text-muted-foreground/50" />
            <p className="text-sm font-medium text-foreground">No background jobs yet</p>
            <p className="max-w-md text-sm text-muted-foreground">
              Durable work appears here the moment it is queued. Nothing is simulated: an
              empty queue means this tenant has none.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border bg-surface-2/50">
                <tr className="text-[0.7rem] uppercase tracking-wide text-muted-foreground">
                  <th className="px-4 py-2 font-medium">Job</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Stage</th>
                  <th className="px-4 py-2 font-medium">Cost</th>
                  <th className="px-4 py-2 font-medium">Created</th>
                  <th className="px-4 py-2 font-medium">Detail</th>
                  <th className="px-4 py-2 text-right font-medium">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {rows.map((row) => (
                  <tr key={row.id} className="align-middle">
                    <td className="px-4 py-2.5">
                      <span className="font-mono text-xs text-foreground">#{row.id}</span>
                      <span className="ml-2 text-muted-foreground">{row.job_type}</span>
                    </td>
                    <td className="px-4 py-2.5">
                      <Badge variant={statusVariant(row.status)}>{row.status}</Badge>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                      {row.completed_stage ?? '—'}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                      ${row.cost_usd.toFixed(4)}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground">
                      {formatTime(row.created_at)}
                    </td>
                    <td className="max-w-[22rem] truncate px-4 py-2.5 text-xs text-muted-foreground">
                      {row.error ??
                        (row.cancelled_by ? `cancelled by ${row.cancelled_by}` : row.workflow_id)}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {TERMINAL.has(row.status) ? (
                        <RowAction
                          icon={RotateCcw}
                          label="Re-queue"
                          busy={busy === row.id}
                          onClick={() => void act(row.id, 'requeue')}
                        />
                      ) : (
                        <RowAction
                          icon={XCircle}
                          label="Cancel"
                          busy={busy === row.id}
                          onClick={() => void act(row.id, 'cancel')}
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

/** One row-level action button, disabled while its call is in flight. */
function RowAction({
  icon: Icon,
  label,
  busy,
  onClick,
}: {
  icon: typeof RotateCcw
  label: string
  busy: boolean
  onClick: () => void
}): ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-surface-2 disabled:opacity-50"
    >
      {busy ? <Loader2 className="size-3 animate-spin" /> : <Icon className="size-3" />}
      {label}
    </button>
  )
}

/** Client entry for the Jobs section — gated on a reachable backend. */
export function JobsMount(): ReactElement {
  // `GET /jobs` is RBAC-scoped and tenant-scoped: hand the child the real session
  // bearer, and hold it back until the persisted session has been restored.
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
      <div className="space-y-4">
        <div>
          <p className="eyebrow mb-1">Durable substrate</p>
          <h1 className="t-hero text-foreground">Jobs</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Background work this tenant owns. Re-queueing passes admission control — the
            in-flight cap and the budget pre-authorisation — and a refusal is shown with the
            reason it carried, never queued out of sight.
          </p>
        </div>
        <JobsView token={session?.token ?? null} />
      </div>
    </BackendGate>
  )
}
