'use client'

import {
  Braces,
  CheckCircle2,
  Circle,
  Loader2,
  ScanText,
  Share2,
  Table2,
  TriangleAlert,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card } from '@/components/ui/Card'
import {
  getIngestProgress,
  type IngestProgress,
  type IngestStage,
  type StageState,
} from '@/lib/api/jobs'
import { cn } from '@/lib/utils'

/** Statuses in which the document is still being worked on, so the log keeps polling. */
const LIVE = new Set(['pending', 'running', 'reconciling'])

/** How often a live ingest is re-read. Six events over minutes — this is not a stream. */
const POLL_MS = 2000

/** Detail keys rendered by their own panel rather than as a chip on the stage card. */
const STRUCTURED = new Set(['quality', 'ocr', 'heading_histogram'])

interface IngestLogProps {
  documentId: number
  token: string | null
}

/**
 * `1.42 s`, or the words for a duration the stage never wrote.
 *
 * These read `—` until the design pass. An em dash in a timing column cannot be
 * told apart from a zero, a pending stage or a broken cell, and DESIGN.md §1 asks
 * for the absence to be stated in the slot the figure would have occupied.
 */
function ms(value: number | null): string {
  if (value === null) return 'not timed'
  if (value < 1000) return `${value} ms`
  return `${(value / 1000).toFixed(2)} s`
}

/** Local wall-clock time from an ISO 8601 timestamp, or the reason there is none. */
function clock(iso: string | null): string {
  if (!iso) return 'no timestamp'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** The badge colour a document status carries. */
function statusVariant(status: string): 'ok' | 'block' | 'risk' | 'agent' | 'neutral' {
  if (status === 'succeeded') return 'ok'
  if (status === 'failed') return 'block'
  if (status === 'reconciling') return 'risk'
  if (status === 'running' || status === 'pending') return 'agent'
  return 'neutral'
}

/** The scalar facts a stage reported, as `key value` pairs a person can scan. */
function chips(detail: Record<string, unknown>): Array<[string, string]> {
  return Object.entries(detail)
    .filter(([key, value]) => !STRUCTURED.has(key) && value !== null && value !== '')
    .filter(([, value]) => typeof value !== 'object')
    .map(([key, value]) => [key.replace(/_/g, ' '), String(value)])
}

/**
 * The live ingest log — one document being read, stage by stage (§4.12, §4.12b).
 *
 * Everything on this screen is a **projection over rows the ingest already committed**:
 * which stages are done comes off `documents.completed_stage`, what each produced comes
 * off the `run_events` entry written inside that stage's own transaction, and the tables,
 * entities and relations come off `chunks.meta`. Nothing is accumulated in the browser,
 * which is why a refresh mid-ingest resumes the view instead of losing it — and why a
 * worker killed mid-pipeline and restarted cannot make this screen claim a stage that
 * never committed.
 *
 * Two panels earn their place beyond the stage strip:
 *
 * - **The parse's own verdict on itself.** A parser that reads a document in the wrong
 *   order does not raise; it produces text that chunks, embeds and answers questions
 *   exactly like correct text. The confidence score, the signals behind it and the
 *   heading histogram are the only place a human can find that out.
 * - **The graph as it is built.** Entities and relations with their mention counts, from
 *   the rows this tenant owns — not a node total from a store nobody can inspect.
 */
export function IngestLog({ documentId, token }: IngestLogProps): ReactElement {
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [error, setError] = useState<string | null>(null)
  const live = useRef(true)

  const read = useCallback(async (): Promise<boolean> => {
    try {
      const next = await getIngestProgress(documentId, token)
      setProgress(next)
      setError(null)
      return LIVE.has(next.status)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to read the ingest log')
      return false
    }
  }, [documentId, token])

  useEffect(() => {
    live.current = true
    let timer: ReturnType<typeof setTimeout> | undefined

    const tick = async (): Promise<void> => {
      const keepGoing = await read()
      if (keepGoing && live.current) timer = setTimeout(() => void tick(), POLL_MS)
    }
    void tick()

    return () => {
      live.current = false
      if (timer) clearTimeout(timer)
    }
  }, [read])

  if (error !== null) {
    return (
      <div className="rounded-lg border border-block/60 bg-block/10 px-4 py-3 text-sm text-block-ink">
        {error}
      </div>
    )
  }

  if (progress === null) {
    return (
      <div className="flex min-h-[120px] items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Reading the ingest record…
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <Header progress={progress} />
      <StageStrip stages={progress.stages} />
      <div className="grid gap-3 lg:grid-cols-2">
        <ParsePanel progress={progress} />
        <GraphPanel progress={progress} />
      </div>
      {progress.tables.length > 0 ? <TablePanel progress={progress} /> : null}
      <LogTail progress={progress} />
    </div>
  )
}

/** Document identity, terminal state, and the three counts a jury asks about. */
function Header({ progress }: { progress: IngestProgress }): ReactElement {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3 p-4">
        <div className="min-w-0">
          <p className="eyebrow mb-0.5">documents · live ingest log</p>
          <p className="truncate text-sm font-medium text-foreground">
            {progress.title ?? progress.filename}
          </p>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
            {progress.filename}
            {progress.workflow_id ? ` · ${progress.workflow_id}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={statusVariant(progress.status)}>{progress.status}</Badge>
          <Badge tone="neutral">
            {progress.page_count === null ? 'pages not parsed' : `${progress.page_count} pages`}
          </Badge>
          <Badge tone="neutral">
            {progress.chunk_count === null ? 'not chunked' : `${progress.chunk_count} chunks`}
          </Badge>
          <Badge tone="neutral">{progress.corpus.embedded} embedded</Badge>
        </div>
      </div>
      {progress.error ? (
        <div className="border-t border-border px-4 py-2.5 text-xs text-block-ink">
          {progress.error}
        </div>
      ) : null}
    </Card>
  )
}

/** The six stages, in pipeline order, each with its state and what it produced. */
function StageStrip({ stages }: { stages: IngestStage[] }): ReactElement {
  return (
    <Card>
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <Braces className="size-4 text-muted-foreground" />
        <p className="eyebrow mb-0">pipeline · read from documents.completed_stage</p>
      </div>
      <ol className="grid gap-px bg-border sm:grid-cols-2 lg:grid-cols-3">
        {stages.map((stage) => (
          <li key={stage.name} className="bg-card p-3.5">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2">
                <StateIcon state={stage.state} />
                <span className="font-mono text-sm font-medium text-foreground">
                  {stage.name}
                </span>
              </span>
              <span
                className={cn(
                  'font-mono text-xs',
                  stage.state === 'failed' ? 'text-block-ink' : 'text-muted-foreground',
                )}
              >
                {stage.state === 'completed' ? ms(stage.duration_ms) : stage.state}
              </span>
            </div>
            <p className="mt-1 font-mono text-[0.7rem] text-muted-foreground">
              {stage.queue}
              {stage.at ? ` · ${clock(stage.at)}` : ''}
            </p>
            {chips(stage.detail).length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1">
                {chips(stage.detail).map(([key, value]) => (
                  <span
                    key={key}
                    className="rounded border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[0.68rem] text-muted-foreground"
                  >
                    {key} <span className="text-foreground">{value}</span>
                  </span>
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ol>
    </Card>
  )
}

/**
 * The state marker for one stage — done, in flight, broken, or still owed.
 *
 * `failed` is its own marker rather than sharing the queued circle. A failed run used to
 * render the stage it died in exactly like the stages that never started, so the only
 * stage the screen named was the last one that *succeeded* — and a reader concluded that
 * one was broken.
 */
function StateIcon({ state }: { state: StageState }): ReactElement {
  if (state === 'completed') return <CheckCircle2 className="size-4 text-ok" />
  if (state === 'running') return <Loader2 className="size-4 animate-spin text-blue-700" />
  if (state === 'failed') return <XCircle className="size-4 text-block" />
  return <Circle className="size-4 text-muted-foreground/50" />
}

/** D-parse (§4.6c) made visible: the score, every signal behind it, and the structure. */
function ParsePanel({ progress }: { progress: IngestProgress }): ReactElement {
  const { parse } = progress
  const levels = Object.entries(parse.heading_histogram)
  const peak = levels.reduce((max, [, count]) => Math.max(max, count), 1)

  return (
    <Card>
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <span className="flex items-center gap-2">
          <ScanText className="size-4 text-muted-foreground" />
          <p className="eyebrow mb-0">parse quality · the gate that catches a silent misread</p>
        </span>
        {parse.confidence === null ? (
          <Badge tone="neutral">not scored</Badge>
        ) : (
          <Badge tone={parse.low ? 'risk' : 'ok'}>
            {parse.low ? <TriangleAlert /> : null}
            {parse.confidence.toFixed(2)} / {parse.threshold.toFixed(2)}
          </Badge>
        )}
      </div>
      <div className="flex flex-col gap-3 p-4">
        {parse.confidence === null ? (
          <p className="text-sm text-muted-foreground">
            The parse stage has not run yet, so nothing has scored this document. A blank
            score means unread — never “no problems found”.
          </p>
        ) : (
          <>
            {parse.low ? (
              <p className="rounded-md border border-risk/60 bg-risk/10 px-3 py-2 text-xs text-risk-ink">
                Indexed and searchable, but its reading order is suspect. A low score
                flags a document; it never blocks it.
              </p>
            ) : null}
            <ul className="flex flex-col gap-1.5">
              {parse.reasons.map((reason) => (
                <li
                  key={reason}
                  className="border-l-2 border-border pl-2.5 text-xs leading-relaxed text-muted-foreground"
                >
                  {reason}
                </li>
              ))}
            </ul>
          </>
        )}

        {parse.ocr_reason ? (
          <div className="rounded-md border border-border bg-surface-2 px-3 py-2">
            <p className="eyebrow mb-1">OCR decision</p>
            <p className="text-xs text-muted-foreground">
              <span className="font-mono text-foreground">
                {parse.ocr_enabled ? 'on' : 'off'}
              </span>{' '}
              — {parse.ocr_reason}
            </p>
          </div>
        ) : null}

        {levels.length > 0 ? (
          <div>
            <p className="eyebrow mb-1.5">heading levels</p>
            <div className="flex flex-col gap-1">
              {levels.map(([level, count]) => (
                <div key={level} className="flex items-center gap-2">
                  <span className="w-8 font-mono text-[0.7rem] text-muted-foreground">
                    h{level}
                  </span>
                  <span
                    className="h-2 rounded-sm bg-blue-400/50"
                    style={{ width: `${Math.max(4, (count / peak) * 100)}%` }}
                  />
                  <span className="font-mono text-[0.7rem] text-muted-foreground">
                    {count}
                  </span>
                </div>
              ))}
            </div>
            <p className="mt-1.5 text-[0.7rem] text-muted-foreground">
              Everything at one level across a long document means the hierarchy is not
              running — which is what a scrambled multi-column parse looks like.
            </p>
          </div>
        ) : null}

        {parse.parser ? (
          <p className="font-mono text-[0.7rem] text-muted-foreground">
            {parse.parser}
            {parse.parse_seconds !== null ? ` · ${parse.parse_seconds.toFixed(2)} s` : ''}
          </p>
        ) : null}
      </div>
    </Card>
  )
}

/** §4.12b — the entities and relations the graph stage wrote onto this tenant's rows. */
function GraphPanel({ progress }: { progress: IngestProgress }): ReactElement {
  const { graph } = progress

  return (
    <Card>
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <span className="flex items-center gap-2">
          <Share2 className="size-4 text-muted-foreground" />
          <p className="eyebrow mb-0">knowledge graph · built from chunks.meta</p>
        </span>
        <span className="flex gap-1.5">
          <Badge tone="graph">{graph.entity_total} entities</Badge>
          <Badge tone="graph">{graph.relation_total} relations</Badge>
        </span>
      </div>
      <div className="flex flex-col gap-3 p-4">
        {graph.entity_total === 0 ? (
          <p className="text-sm text-muted-foreground">
            The graph stage has not run yet. This panel fills in as entities are
            extracted — it is read from the rows, not from the graph store.
          </p>
        ) : (
          <>
            <div>
              <p className="eyebrow mb-1.5">entities · by mentions</p>
              <div className="flex flex-wrap gap-1.5">
                {graph.entities.map((entity) => (
                  <span
                    key={entity.id}
                    title={entity.id}
                    className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface-2 px-2 py-0.5 text-xs text-foreground"
                  >
                    {entity.label}
                    <span className="font-mono text-[0.65rem] text-muted-foreground">
                      {entity.kind}
                    </span>
                    <span className="font-mono text-[0.65rem] text-blue-600">
                      ×{entity.mentions}
                    </span>
                  </span>
                ))}
              </div>
            </div>
            {graph.relations.length > 0 ? (
              <div>
                <p className="eyebrow mb-1.5">relations</p>
                <ul className="flex flex-col gap-1">
                  {graph.relations.map((relation) => (
                    <li
                      key={`${relation.source}|${relation.phrase}|${relation.target}`}
                      className="text-xs text-muted-foreground"
                    >
                      <span className="text-foreground">{relation.source}</span>
                      <span className="mx-1.5 font-mono text-[0.7rem]">
                        —{relation.phrase}→
                      </span>
                      <span className="text-foreground">{relation.target}</span>
                      <span className="ml-1.5 font-mono text-[0.65rem]">
                        ×{relation.mentions}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        )}
        {graph.extractor ? (
          <p className="font-mono text-[0.7rem] text-muted-foreground">
            extractor {graph.extractor}
          </p>
        ) : null}
      </div>
    </Card>
  )
}

/** The tables lifted out as their own chunks, and which of them were summarised (D8). */
function TablePanel({ progress }: { progress: IngestProgress }): ReactElement {
  return (
    <Card>
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <span className="flex items-center gap-2">
          <Table2 className="size-4 text-muted-foreground" />
          <p className="eyebrow mb-0">tables · own chunks, shape kept</p>
        </span>
        <Badge tone="neutral">
          {progress.corpus.summarised} of {progress.corpus.tables} summarised
        </Badge>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border bg-surface-2/50">
            <tr className="text-[0.7rem] uppercase tracking-wide text-muted-foreground">
              <th className="px-4 py-2 font-medium">Caption</th>
              <th className="px-4 py-2 font-medium">Shape</th>
              <th className="px-4 py-2 font-medium">Summary</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/70">
            {progress.tables.map((table, index) => (
              <tr key={`${table.caption ?? 'table'}-${index}`}>
                <td className="max-w-[28rem] truncate px-4 py-2 text-xs text-foreground">
                  {table.caption ?? <span className="text-muted-foreground">uncaptioned</span>}
                </td>
                <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
                  {table.rows ?? '?'} × {table.cols ?? '?'}
                </td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  {table.summarised ? (
                    <Badge tone="ok">written</Badge>
                  ) : (
                    (table.reason ?? 'below the size threshold')
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/** The chronological tail — every run of this document, oldest first. */
function LogTail({ progress }: { progress: IngestProgress }): ReactElement {
  return (
    <Card>
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <Braces className="size-4 text-muted-foreground" />
        <p className="eyebrow mb-0">run_events · the durable record, replayed</p>
      </div>
      {progress.entries.length === 0 ? (
        <p className="px-4 py-4 text-sm text-muted-foreground">
          No entries yet. The first appears when a stage commits — the entry and the stage
          are one transaction, so neither can exist without the other.
        </p>
      ) : (
        <ul className="max-h-64 divide-y divide-border/70 overflow-y-auto">
          {progress.entries.map((entry) => (
            <li
              key={`${entry.kind}-${entry.seq}-${entry.ts}`}
              className="flex gap-3 px-4 py-1.5 font-mono text-xs"
            >
              <span className="shrink-0 text-muted-foreground">{clock(entry.ts)}</span>
              <span
                className={cn(
                  'truncate',
                  entry.kind === 'ingest_finished' ? 'text-foreground' : 'text-muted-foreground',
                )}
              >
                {entry.message}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
