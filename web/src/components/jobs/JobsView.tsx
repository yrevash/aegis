'use client'

import {
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Layers,
  Loader2,
  RefreshCw,
  RotateCcw,
  TriangleAlert,
  XCircle,
} from 'lucide-react'
import { Fragment, useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { DataPanel } from '@/components/ui/DataPanel'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { EmptyState, LoadingState } from '@/components/primitives/States'
import { BackendGate, BackendUnavailable } from '@/components/shared/BackendGate'
import { PipelineHealthPanel } from '@/components/health/PipelineHealthView'
import { CorpusPanel } from '@/components/jobs/CorpusPanel'
import { IngestLog } from '@/components/jobs/IngestLog'
import { PipelineIso } from '@/components/jobs/PipelineIso'
import { UploadPanel } from '@/components/jobs/UploadPanel'
import { cancelJob, getJobs, JobsApiError, requeueJob, type JobRunRow } from '@/lib/api/jobs'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'

/**
 * How often the queue is re-read, in ms.
 *
 * Two cadences rather than one, because a fixed interval has to choose between
 * being useless and being rude: 15 s is too slow to watch a stage move, and 4 s
 * against a quiet queue is nine hundred pointless round trips an hour. So the
 * page asks quickly exactly while `job_runs` says something is in flight, and
 * settles back the moment nothing is. A hidden tab asks for nothing at all.
 */
const POLL_BUSY_MS = 4_000
const POLL_IDLE_MS = 15_000

/** Load state for the jobs fetch. */
type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: JobRunRow[] }

/** Statuses in which the orchestrator may still be holding a worker slot. */
const IN_FLIGHT = new Set(['pending', 'running', 'reconciling'])

/** Statuses a job can never leave — a re-queue is offered, a cancel is not. */
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled'])

/** The one focus treatment on this screen: the ring token, at 2px, always visible. */
const FOCUS =
  'outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background'

/** The four cuts of the queue a person actually asks for. */
type Filter = 'all' | 'flight' | 'failed' | 'done'

const FILTERS: Array<{ id: Filter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'flight', label: 'In flight' },
  { id: 'failed', label: 'Failed' },
  { id: 'done', label: 'Succeeded' },
]

function matches(row: JobRunRow, filter: Filter): boolean {
  if (filter === 'all') return true
  if (filter === 'flight') return IN_FLIGHT.has(row.status)
  if (filter === 'failed') return row.status === 'failed' || row.status === 'cancelled'
  return row.status === 'succeeded'
}

/** The signal colour each job status carries. Always beside the word, never alone. */
function statusVariant(status: string): 'ok' | 'block' | 'risk' | 'agent' | 'neutral' {
  if (status === 'succeeded') return 'ok'
  if (status === 'failed') return 'block'
  if (status === 'reconciling') return 'risk'
  if (status === 'running' || status === 'pending') return 'agent'
  return 'neutral'
}

/** Local wall-clock time from an ISO 8601 timestamp. */
function formatTime(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * A cell whose value the record does not carry.
 *
 * These were all an em dash, in eight cells across three tables, and an em dash
 * is a glyph a reader has to decode before they can tell "the worker never wrote
 * this" from "this is zero" from "the column is broken". DESIGN.md §1 asks for a
 * stated absence in the slot the value would have occupied, so that is what this
 * is — short enough for a table cell, and read aloud correctly.
 */
export function NotRecorded({ what = 'not recorded' }: { what?: string }): ReactElement {
  return <span className="text-xs text-muted-foreground italic">{what}</span>
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
 *
 * The screen is now two things rather than thirty-five panels: the funnel above
 * ({@link PipelineIso} — the six ingest stages as isometric solids, height by the
 * number of runs that committed each one) and this table beneath it. The prose
 * that used to sit between them is in the column tips and the stage tips; the
 * refusal banner and the stated absences stay, because those are the product.
 */
export function JobsView({ token }: JobsViewProps): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [busy, setBusy] = useState<number | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  // Which job's ingest log is expanded. One at a time: the log polls while its document
  // is still being read, and six open panels would be six polls saying the same thing.
  const [openJob, setOpenJob] = useState<number | null>(null)
  // Which document the corpus panel opened directly. A document whose ingest never
  // started owns no job row, so it is reachable *only* this way — which is the whole
  // reason `GET /documents` exists beside `GET /jobs`.
  const [openDocument, setOpenDocument] = useState<number | null>(null)
  // Bumped after an upload so the corpus listing reloads with the jobs table.
  const [corpusKey, setCorpusKey] = useState(0)
  const [notice, setNotice] = useState<{ kind: 'ok' | 'refused' | 'error'; text: string } | null>(
    null,
  )

  // Epoch ms of the last read that actually agreed with the database. Never "now":
  // a live surface that stamps itself on render says it is fresh when it is frozen.
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const [polling, setPolling] = useState(true)

  /**
   * Re-read `GET /jobs`.
   *
   * `background` is what makes the timer safe to run: a poll that fails must not
   * replace a screen full of real rows with a backend-unavailable panel, because
   * one dropped request is not an outage. A background failure therefore leaves
   * the last good rows and the last good timestamp standing — the stamp stops
   * advancing, which is itself the signal that something is wrong.
   */
  const refresh = useCallback(
    async (background = false): Promise<void> => {
      try {
        const res = await getJobs(token)
        setLoad({ status: 'ready', rows: res.rows })
        setUpdatedAt(Date.now())
      } catch (error: unknown) {
        if (background) return
        setLoad({
          status: 'error',
          message: error instanceof Error ? error.message : 'Failed to load jobs',
        })
      }
    },
    [token],
  )

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

  const rows = useMemo(() => (load.status === 'ready' ? load.rows : []), [load])
  const shown = useMemo(() => rows.filter((row) => matches(row, filter)), [rows, filter])
  const inFlight = rows.filter((row) => IN_FLIGHT.has(row.status)).length

  /**
   * The live feed.
   *
   * The cadence is a function of the queue's own state, so the interval is torn
   * down and rebuilt only when work starts or stops — not on every poll. A row
   * action holds the timer off while it is in flight, because a re-queue and a
   * poll racing each other is how a button appears to undo itself.
   */
  useEffect(() => {
    if (typeof document === 'undefined') return
    const period = inFlight > 0 ? POLL_BUSY_MS : POLL_IDLE_MS
    const timer = setInterval(() => {
      if (document.hidden || busy !== null) return
      void refresh(true)
    }, period)
    const onVisibility = (): void => {
      const visible = !document.hidden
      setPolling(visible)
      if (visible) void refresh(true)
    }
    document.addEventListener('visibilitychange', onVisibility)
    setPolling(!document.hidden)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [inFlight, busy, refresh])

  if (load.status === 'error') return <BackendUnavailable detail={load.message} />

  const NoticeIcon =
    notice?.kind === 'ok' ? CircleCheck : notice?.kind === 'refused' ? TriangleAlert : CircleAlert

  return (
    <div className="flex flex-col gap-4">
      {/* The pipeline before the queue: one glance at where the corpus actually
          is, then the rows that make it up. */}
      <PipelineIso
        rows={rows}
        loading={load.status === 'loading'}
        updatedAt={updatedAt}
        polling={polling}
      />

      {notice ? (
        <p
          role="status"
          className={cn(
            'flex items-start gap-2 rounded-lg border px-4 py-3 text-sm leading-relaxed',
            notice.kind === 'ok' && 'border-ok bg-ok/10 text-foreground',
            notice.kind === 'refused' && 'border-risk bg-risk/10 text-foreground',
            notice.kind === 'error' && 'border-block bg-block/10 text-foreground',
          )}
        >
          <NoticeIcon
            aria-hidden
            className={cn(
              'mt-0.5 size-4 shrink-0',
              notice.kind === 'ok' && 'text-ok-ink',
              notice.kind === 'refused' && 'text-risk-ink',
              notice.kind === 'error' && 'text-block-ink',
            )}
          />
          <span>
            <span className="font-medium">
              {notice.kind === 'ok' ? 'Done. ' : notice.kind === 'refused' ? 'Refused. ' : 'Failed. '}
            </span>
            {notice.text}
          </span>
        </p>
      ) : null}

      <DataPanel
        eyebrow="job_runs · the record layer"
        title="Queue"
        maxHeight="30rem"
        actions={
          <button
            type="button"
            onClick={() => void refresh()}
            className={`inline-flex h-9 touch-manipulation items-center gap-2 rounded-lg border border-border bg-card px-3 text-sm font-medium text-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 ${FOCUS}`}
          >
            <RefreshCw className="size-4" aria-hidden />
            Refresh
          </button>
        }
        toolbar={
          <>
            <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Filter the queue">
              {FILTERS.map((option) => {
                const active = filter === option.id
                return (
                  <button
                    key={option.id}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setFilter(option.id)}
                    className={cn(
                      'h-8 touch-manipulation rounded-full border px-3 text-xs font-medium transition-colors duration-[--dur-fast]',
                      FOCUS,
                      active
                        ? 'border-blue-600 bg-blue-50 text-blue-700'
                        : 'border-border bg-card text-muted-foreground hover:bg-surface-2',
                    )}
                  >
                    {option.label}
                  </button>
                )
              })}
            </div>
            <p className="ml-auto text-xs text-muted-foreground">
              {load.status === 'loading' ? (
                'Reading the queue…'
              ) : (
                <>
                  <Figure className="text-foreground">{shown.length}</Figure> of{' '}
                  <Figure className="text-foreground">{rows.length}</Figure> shown ·{' '}
                  <Figure className="text-foreground">{inFlight}</Figure> in flight
                </>
              )}
            </p>
          </>
        }
      >
        {load.status === 'loading' ? (
          <LoadingState rows={4} label="Reading the job queue…" />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Layers}
            title="No background jobs yet"
            body="Durable work appears here the moment it is queued, newest first. Nothing is simulated: an empty queue means this tenant has none."
          />
        ) : shown.length === 0 ? (
          <EmptyState
            icon={Layers}
            title={`No ${FILTERS.find((f) => f.id === filter)?.label.toLowerCase()} jobs`}
            body="The filter above matched nothing in this tenant's queue."
          />
        ) : (
          <table className="w-full min-w-[56rem] text-left text-sm">
            <thead className="sticky top-0 z-10 bg-surface-2">
              <tr className="border-b border-border">
                <Th>Job</Th>
                <Th>Status</Th>
                <Th tip="The last stage that committed inside its own transaction. A blank cell means the run never wrote one — not that it started at the beginning.">
                  Stage
                </Th>
                <Th tip="Metered spend for this run alone, from the usage ledger. It is charged against the tenant's cap whether the run succeeds or fails.">
                  Cost
                </Th>
                <Th>Created</Th>
                <Th tip="The worker's own sentence when it failed, who cancelled it, or the workflow id when neither applies.">
                  Detail
                </Th>
                <Th>Log</Th>
                <Th className="text-right">Action</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {shown.map((row) => (
                <Fragment key={row.id}>
                  <tr className="align-middle transition-colors duration-[--dur-fast] hover:bg-surface-2/60">
                    <td className="px-3 py-2">
                      <Figure className="text-foreground">#{row.id}</Figure>
                      <span className="ml-2 text-xs text-muted-foreground">{row.job_type}</span>
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={statusVariant(row.status)}>{row.status}</Badge>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {row.completed_stage ? (
                        <Figure className="text-muted-foreground">{row.completed_stage}</Figure>
                      ) : (
                        <NotRecorded what="none committed" />
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Figure className="text-muted-foreground">${row.cost_usd.toFixed(4)}</Figure>
                    </td>
                    <td className="px-3 py-2 text-xs whitespace-nowrap text-muted-foreground">
                      {formatTime(row.created_at) ?? <NotRecorded />}
                    </td>
                    <td className="max-w-[20rem] truncate px-3 py-2 text-xs text-muted-foreground">
                      {row.error ??
                        (row.cancelled_by ? `cancelled by ${row.cancelled_by}` : row.workflow_id)}
                    </td>
                    <td className="px-3 py-2">
                      {row.document_id === null ? (
                        <NotRecorded what="no document" />
                      ) : (
                        <RowAction
                          icon={openJob === row.id ? ChevronDown : ChevronRight}
                          label={openJob === row.id ? 'Hide' : 'Watch'}
                          hint={`the ingest log for job ${row.id}`}
                          expanded={openJob === row.id}
                          busy={false}
                          onClick={() => setOpenJob(openJob === row.id ? null : row.id)}
                        />
                      )}
                    </td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      {TERMINAL.has(row.status) ? (
                        <RowAction
                          icon={RotateCcw}
                          label="Re-queue"
                          hint={`job ${row.id}`}
                          busy={busy === row.id}
                          onClick={() => void act(row.id, 'requeue')}
                        />
                      ) : (
                        <RowAction
                          icon={XCircle}
                          label="Cancel"
                          hint={`job ${row.id}`}
                          busy={busy === row.id}
                          onClick={() => void act(row.id, 'cancel')}
                        />
                      )}
                    </td>
                  </tr>
                  {openJob === row.id && row.document_id !== null ? (
                    <tr>
                      <td colSpan={8} className="bg-surface-2/40 p-3">
                        <IngestLog documentId={row.document_id} token={token} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </DataPanel>

      {/* The front door and the corpus, side by side beneath the queue: an upload
          is what puts a document into it, and `documents` is what came out. */}
      <div className="grid items-start gap-4 xl:grid-cols-2">
        <UploadPanel
          token={token}
          onUploaded={() => {
            void refresh()
            setCorpusKey((n) => n + 1)
          }}
        />
        <CorpusPanel
          token={token}
          reloadKey={corpusKey}
          onOpen={(documentId) => setOpenDocument(openDocument === documentId ? null : documentId)}
        />
      </div>
      {openDocument !== null ? (
        <div className="rounded-lg border border-border bg-surface-2/40 p-4">
          <IngestLog documentId={openDocument} token={token} />
        </div>
      ) : null}
    </div>
  )
}

/** One column heading, with the sentence that used to sit under the table. */
function Th({
  children,
  tip,
  className,
}: {
  children: ReactElement | string
  tip?: string
  className?: string
}): ReactElement {
  return (
    <th scope="col" className={cn('eyebrow px-3 py-2 font-medium', className)}>
      <span className={cn('inline-flex items-center gap-1', className === 'text-right' && 'justify-end')}>
        {children}
        {tip ? <InfoTip label={`About the ${children} column`}>{tip}</InfoTip> : null}
      </span>
    </th>
  )
}

/**
 * One row-level action button, disabled while its call is in flight.
 *
 * `hint` is what makes a column of eleven identical "Cancel" buttons usable from a
 * screen reader — the visible label is the same on every row, so the accessible
 * name has to carry the row.
 */
function RowAction({
  icon: Icon,
  label,
  hint,
  busy,
  expanded,
  onClick,
}: {
  icon: typeof RotateCcw
  label: string
  hint: string
  busy: boolean
  expanded?: boolean
  onClick: () => void
}): ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-expanded={expanded}
      className={`inline-flex h-8 touch-manipulation items-center gap-1.5 whitespace-nowrap rounded-lg border border-border bg-card px-2.5 text-xs font-medium text-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 disabled:opacity-50 ${FOCUS}`}
    >
      {busy ? (
        <Loader2 className="size-3 animate-spin motion-reduce:animate-none" aria-hidden />
      ) : (
        <Icon className="size-3" aria-hidden />
      )}
      {label}
      <span className="sr-only"> {hint}</span>
    </button>
  )
}

/** Client entry for the Jobs section — gated on a reachable backend. */
export function JobsMount(): ReactElement {
  // `GET /jobs` is RBAC-scoped and tenant-scoped: hand the child the real session
  // bearer, and hold it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()
  const [deep, setDeep] = useState(false)

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
        <PageHeader
          eyebrow="Durable substrate"
          title="Jobs"
          actions={
            <>
              <InfoTip label="How re-queue and cancel behave">
                Re-queueing passes admission control — the in-flight cap and the budget
                pre-authorisation — and a refusal is shown with the reason it carried, never
                queued out of sight. Cancel signals the orchestrator and records who asked, on
                the row.
              </InfoTip>
              <button
                type="button"
                aria-expanded={deep}
                onClick={() => setDeep((open) => !open)}
                className={`inline-flex h-9 touch-manipulation items-center gap-2 rounded-lg border border-border bg-card px-3 text-sm font-medium text-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 ${FOCUS}`}
              >
                {deep ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
                Pipeline health
              </button>
            </>
          }
        />
        <JobsView token={session?.token ?? null} />
        {/* §7.10 is an aggregation over exactly the rows this page lists — worker
            liveness, per-stage timings, the dependency table and everything it
            states it cannot measure. It is nine panels, so it is behind a
            disclosure rather than deleted: compressed, not lost. */}
        {deep ? <PipelineHealthPanel /> : null}
      </div>
    </BackendGate>
  )
}
