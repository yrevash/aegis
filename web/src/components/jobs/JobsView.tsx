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
import { Fragment, useCallback, useEffect, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card } from '@/components/ui/Card'
import { Figure } from '@/components/primitives/Figure'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { EmptyState, LoadingState } from '@/components/primitives/States'
import { BackendGate, BackendUnavailable } from '@/components/shared/BackendGate'
import { PipelineHealthPanel } from '@/components/health/PipelineHealthView'
import { CorpusPanel } from '@/components/jobs/CorpusPanel'
import { IngestLog } from '@/components/jobs/IngestLog'
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

/** The one focus treatment on this screen: the ring token, at 2px, always visible. */
const FOCUS =
  'outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background'

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
 * **Watch** expands the row into `IngestLog` — the live, stage-by-stage record of the
 * document being read (§4.12). It is a projection over rows the ingest already
 * committed, so it survives a refresh and cannot claim a stage a killed worker never
 * finished.
 */
export function JobsView({ token }: JobsViewProps): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [busy, setBusy] = useState<number | null>(null)
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
  const NoticeIcon =
    notice?.kind === 'ok' ? CircleCheck : notice?.kind === 'refused' ? TriangleAlert : CircleAlert

  return (
    <div className="flex flex-col gap-4">
      {/* The front door: an upload is what puts a document into this queue at all. */}
      <UploadPanel
        token={token}
        onUploaded={() => {
          void refresh()
          setCorpusKey((n) => n + 1)
        }}
      />

      {/* What this tenant has actually ingested — the answer to "show me your corpus".
          Read from `documents`, not from the job queue, so a document whose ingest never
          started is visible here even though it owns no job row. */}
      <CorpusPanel
        token={token}
        reloadKey={corpusKey}
        onOpen={(documentId) => setOpenDocument(openDocument === documentId ? null : documentId)}
      />
      {openDocument !== null ? (
        <div className="rounded-lg border border-border bg-surface-2/40 p-4">
          <IngestLog documentId={openDocument} token={token} />
        </div>
      ) : null}

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 p-4 md:p-5">
          <div className="flex items-center gap-3">
            <Layers className="size-5 text-muted-foreground" aria-hidden />
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
            className={`inline-flex h-11 touch-manipulation items-center gap-2 rounded-lg border border-border bg-card px-3 text-sm font-medium text-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 ${FOCUS}`}
          >
            <RefreshCw className="size-4" aria-hidden />
            Refresh
          </button>
        </div>
      </Card>

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

      <Card className="overflow-hidden">
        {load.status === 'loading' ? (
          <div className="p-4">
            <LoadingState rows={4} label="Reading the job queue…" />
          </div>
        ) : rows.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Layers}
              title="No background jobs yet"
              body="Durable work appears here the moment it is queued, newest first. Nothing is simulated: an empty queue means this tenant has none. Upload a document above to put one in it."
            />
          </div>
        ) : (
          // Eight columns will not fit a phone, and squeezing them would make every
          // one unreadable rather than one of them scrollable. The table keeps its
          // width and scrolls inside this box; the page body never does.
          <div className="w-full overflow-x-auto">
            <table className="w-full min-w-[64rem] text-left text-sm">
              <thead className="border-b border-border bg-surface-2/50">
                <tr>
                  {['Job', 'Status', 'Stage', 'Cost', 'Created', 'Detail', 'Ingest log'].map((h) => (
                    <th key={h} scope="col" className="eyebrow px-4 py-2.5 font-medium">
                      {h}
                    </th>
                  ))}
                  <th scope="col" className="eyebrow px-4 py-2.5 text-right font-medium">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => (
                  <Fragment key={row.id}>
                    <tr className="align-middle transition-colors duration-[--dur-fast] hover:bg-surface-2/60">
                      <td className="px-4 py-3">
                        <Figure className="text-foreground">#{row.id}</Figure>
                        <span className="ml-2 text-muted-foreground">{row.job_type}</span>
                      </td>
                      <td className="px-4 py-3">
                        <Badge tone={statusVariant(row.status)}>{row.status}</Badge>
                      </td>
                      <td className="px-4 py-3">
                        {row.completed_stage ? (
                          <Figure className="text-muted-foreground">{row.completed_stage}</Figure>
                        ) : (
                          <NotRecorded what="no stage committed" />
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <Figure className="text-muted-foreground">
                          ${row.cost_usd.toFixed(4)}
                        </Figure>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">
                        {formatTime(row.created_at) ?? <NotRecorded />}
                      </td>
                      <td className="max-w-[22rem] truncate px-4 py-3 text-xs text-muted-foreground">
                        {row.error ??
                          (row.cancelled_by ? `cancelled by ${row.cancelled_by}` : row.workflow_id)}
                      </td>
                      <td className="px-4 py-3">
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
                      <td className="px-4 py-3 text-right">
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
                        <td colSpan={8} className="bg-surface-2/40 p-4">
                          <IngestLog documentId={row.document_id} token={token} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
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
      className={`inline-flex h-9 touch-manipulation items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 text-xs font-medium text-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 disabled:opacity-50 ${FOCUS}`}
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
        <SectionHeader
          as="h1"
          eyebrow="Durable substrate"
          title="Jobs"
          note={
            <>
              Background work this tenant owns. Re-queueing passes admission control — the
              in-flight cap and the budget pre-authorisation — and a refusal is shown with the
              reason it carried, never queued out of sight. <strong>Watch</strong> opens the
              live ingest log for a document: which stage is running, what each one produced,
              the parse&rsquo;s own confidence in itself, and the graph as it is extracted.
            </>
          }
        />
        {/* The pipeline before the queue. §7.10 is an aggregation over exactly the
            rows this page then lists — depth, the oldest pending job, the failure
            count, the per-stage timings the ingest already commits — so it belongs
            above them rather than on a page of its own that reads the same tables. */}
        <PipelineHealthPanel />
        <JobsView token={session?.token ?? null} />
      </div>
    </BackendGate>
  )
}
