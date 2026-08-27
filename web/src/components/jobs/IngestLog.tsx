'use client'

import {
  Braces,
  CheckCircle2,
  Circle,
  Loader2,
  ScanText,
  Share2,
  TriangleAlert,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Absence } from '@/components/primitives/Receipt'
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
 * It used to say all of that on the page, in twenty-one prose blocks stacked down six
 * cards. The mechanism is now in {@link InfoTip}s, the stage cards are one
 * {@link StageTimeline}, and the three "this has not run yet" paragraphs are
 * {@link Absence}es in the slot the figure would have occupied — the honesty is
 * identical and it fits on a screen.
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
        <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
        Reading the ingest record…
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <Card>
        <Header progress={progress} />
        <div className="border-t border-border px-4 py-3">
          <StageTimeline stages={progress.stages} />
        </div>
      </Card>
      <div className="grid gap-3 xl:grid-cols-2">
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
    <>
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
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
        <div className="border-t border-border px-4 py-2 text-xs text-block-ink">
          {progress.error}
        </div>
      ) : null}
    </>
  )
}

/**
 * The six stages as one connected timeline rather than six stacked cards.
 *
 * A stage card carried its name, its state, its queue, its wall-clock time and up
 * to six `key value` chips — six of those is most of a screen, and none of it is
 * read until something goes wrong. What a reader wants at a glance is *how far it
 * got and how long each step took*; the chips are a hover away, which is exactly
 * the trade DESIGN.md §4 asks for.
 */
function StageTimeline({ stages }: { stages: IngestStage[] }): ReactElement {
  return (
    <>
      <div className="mb-2 flex items-center gap-1.5">
        <Braces className="size-3.5 text-muted-foreground" aria-hidden />
        <p className="eyebrow mb-0">pipeline · documents.completed_stage</p>
        <InfoTip label="Where this timeline comes from">
          Each step is read off the row the stage committed inside its own transaction — the
          output and the `completed_stage` bump land together or not at all. A worker killed
          mid-pipeline and restarted therefore cannot make this strip claim a stage that never
          finished.
        </InfoTip>
      </div>
      <ol className="flex min-w-0 items-start gap-0 overflow-x-auto overscroll-x-contain pb-1">
        {stages.map((stage, i) => {
          const detail = chips(stage.detail)
          return (
            <li key={stage.name} className="flex min-w-0 flex-1 basis-0 items-start">
              {i > 0 ? (
                <span
                  aria-hidden
                  className={cn(
                    'mt-[9px] h-px min-w-3 flex-1',
                    stage.state === 'queued' ? 'bg-border' : 'bg-blue-400',
                  )}
                />
              ) : null}
              <div className="flex min-w-[6.5rem] flex-col items-center gap-1 px-1 text-center">
                <StateIcon state={stage.state} />
                <span className="inline-flex items-center gap-1">
                  <span className="font-mono text-xs font-medium text-foreground">
                    {stage.name}
                  </span>
                  {detail.length > 0 ? (
                    <InfoTip label={`What the ${stage.name} stage produced`}>
                      <span className="flex flex-col gap-0.5">
                        <span className="text-muted-foreground">
                          {stage.queue} queue{stage.at ? ` · ${clock(stage.at)}` : ''}
                        </span>
                        {detail.map(([key, value]) => (
                          <span key={key} className="font-mono">
                            {key} <span className="text-foreground">{value}</span>
                          </span>
                        ))}
                      </span>
                    </InfoTip>
                  ) : null}
                </span>
                <span
                  className={cn(
                    'tabular font-mono text-[0.6875rem]',
                    stage.state === 'failed' ? 'text-block-ink' : 'text-muted-foreground',
                  )}
                >
                  {stage.state === 'completed' ? ms(stage.duration_ms) : stage.state}
                </span>
              </div>
              {i < stages.length - 1 ? (
                <span
                  aria-hidden
                  className={cn(
                    'mt-[9px] h-px min-w-3 flex-1',
                    stages[i + 1].state === 'queued' ? 'bg-border' : 'bg-blue-400',
                  )}
                />
              ) : null}
            </li>
          )
        })}
      </ol>
    </>
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
  if (state === 'completed') return <CheckCircle2 className="size-[18px] text-ok" aria-label="completed" />
  if (state === 'running')
    return (
      <Loader2 className="size-[18px] animate-spin text-blue-700 motion-reduce:animate-none" aria-label="running" />
    )
  if (state === 'failed') return <XCircle className="size-[18px] text-block" aria-label="failed" />
  return <Circle className="size-[18px] text-muted-foreground/50" aria-label="queued" />
}

/** D-parse (§4.6c) made visible: the score, every signal behind it, and the structure. */
function ParsePanel({ progress }: { progress: IngestProgress }): ReactElement {
  const { parse } = progress
  const levels = Object.entries(parse.heading_histogram)
  const peak = levels.reduce((max, [, count]) => Math.max(max, count), 1)

  return (
    <Card className="flex flex-col">
      <CardHeader
        as="h3"
        eyebrow="parse quality"
        title={
          <span className="inline-flex items-center gap-1.5">
            <ScanText className="size-4 text-muted-foreground" aria-hidden />
            Reading order
            <InfoTip label="Why parse quality is scored at all">
              A parser that reads a document in the wrong order does not raise. It produces
              text that chunks, embeds and answers questions exactly like correct text — so
              this score, and the signals behind it, are the only place a human can find out.
            </InfoTip>
          </span>
        }
        actions={
          parse.confidence === null ? (
            <Badge tone="neutral">not scored</Badge>
          ) : (
            <Badge tone={parse.low ? 'risk' : 'ok'}>
              {parse.low ? <TriangleAlert /> : null}
              {parse.confidence.toFixed(2)} / {parse.threshold.toFixed(2)}
            </Badge>
          )
        }
      />
      <CardBody className="flex flex-col gap-3 py-4 md:py-4">
        {parse.confidence === null ? (
          <Absence
            figure="Parse confidence"
            why="the parse stage has not run, so nothing has scored this document"
            needed="A blank score means unread — never “no problems found”."
          />
        ) : (
          <>
            {parse.low ? (
              <p className="rounded-md border border-risk/60 bg-risk/10 px-3 py-1.5 text-xs text-risk-ink">
                Reading order suspect. Indexed and searchable anyway — a low score flags, it
                never blocks.
              </p>
            ) : null}
            <ul className="flex flex-col gap-1">
              {parse.reasons.map((reason) => (
                <li
                  key={reason}
                  className="border-l-2 border-border pl-2.5 text-xs leading-snug text-muted-foreground"
                >
                  {reason}
                </li>
              ))}
            </ul>
          </>
        )}

        {parse.ocr_reason ? (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="rounded-full border border-border bg-surface-2 px-1.5 py-px font-mono text-[0.6875rem] text-foreground">
              OCR {parse.ocr_enabled ? 'on' : 'off'}
            </span>
            <InfoTip label="Why OCR was or was not used">{parse.ocr_reason}</InfoTip>
          </p>
        ) : null}

        {levels.length > 0 ? (
          <div>
            <p className="eyebrow mb-1.5 inline-flex items-center gap-1">
              heading levels
              <InfoTip label="How to read the heading histogram">
                Everything at one level across a long document means the hierarchy is not
                running — which is what a scrambled multi-column parse looks like.
              </InfoTip>
            </p>
            <div className="flex flex-col gap-1">
              {levels.map(([level, count]) => (
                <div key={level} className="flex items-center gap-2">
                  <span className="w-7 font-mono text-[0.7rem] text-muted-foreground">
                    h{level}
                  </span>
                  <span
                    className="h-2 rounded-sm bg-blue-400/50"
                    style={{ width: `${Math.max(4, (count / peak) * 100)}%` }}
                  />
                  <Figure className="text-[0.7rem] text-muted-foreground">{count}</Figure>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {parse.parser ? (
          <p className="mt-auto font-mono text-[0.7rem] text-muted-foreground">
            {parse.parser}
            {parse.parse_seconds !== null ? ` · ${parse.parse_seconds.toFixed(2)} s` : ''}
          </p>
        ) : null}
      </CardBody>
    </Card>
  )
}

/** §4.12b — the entities and relations the graph stage wrote onto this tenant's rows. */
function GraphPanel({ progress }: { progress: IngestProgress }): ReactElement {
  const { graph } = progress

  return (
    <Card className="flex flex-col">
      <CardHeader
        as="h3"
        eyebrow="knowledge graph"
        title={
          <span className="inline-flex items-center gap-1.5">
            <Share2 className="size-4 text-muted-foreground" aria-hidden />
            Entities and relations
            <InfoTip label="Where the graph figures come from">
              Read from `chunks.meta` — the rows this tenant owns — not a node total from a
              graph store nobody can inspect.
            </InfoTip>
          </span>
        }
        actions={
          <span className="flex gap-1.5">
            <Badge tone="graph">{graph.entity_total} entities</Badge>
            <Badge tone="graph">{graph.relation_total} relations</Badge>
          </span>
        }
      />
      <CardBody className="flex flex-col gap-3 py-4 md:py-4">
        {graph.entity_total === 0 ? (
          <Absence
            figure="Entities and relations"
            why="the graph stage has not run for this document yet"
            needed="This panel fills in as the extractor commits rows."
          />
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
                    <span className="font-mono text-[0.6875rem] text-muted-foreground">
                      {entity.kind}
                    </span>
                    <span className="font-mono text-[0.6875rem] text-blue-700">
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
                      <span className="ml-1.5 font-mono text-[0.6875rem]">
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
          <p className="mt-auto font-mono text-[0.7rem] text-muted-foreground">
            extractor {graph.extractor}
          </p>
        ) : null}
      </CardBody>
    </Card>
  )
}

/** The tables lifted out as their own chunks, and which of them were summarised (D8). */
function TablePanel({ progress }: { progress: IngestProgress }): ReactElement {
  return (
    <DataPanel
      as="h3"
      eyebrow="tables · own chunks, shape kept"
      title="Tables"
      collapsible
      maxHeight="16rem"
      actions={
        <Badge tone="neutral">
          {progress.corpus.summarised} of {progress.corpus.tables} summarised
        </Badge>
      }
    >
      <table className="w-full min-w-[32rem] text-left text-sm">
        <thead className="sticky top-0 z-10 bg-surface-2">
          <tr className="border-b border-border">
            <th className="eyebrow px-3 py-2 font-medium">Caption</th>
            <th className="eyebrow px-3 py-2 font-medium">Shape</th>
            <th className="eyebrow px-3 py-2 font-medium">Summary</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/70">
          {progress.tables.map((table, index) => (
            <tr key={`${table.caption ?? 'table'}-${index}`}>
              <td className="max-w-[26rem] truncate px-3 py-1.5 text-xs text-foreground">
                {table.caption ?? <span className="text-muted-foreground italic">uncaptioned</span>}
              </td>
              <td className="px-3 py-1.5">
                <Figure className="text-xs text-muted-foreground">
                  {table.rows ?? '?'} × {table.cols ?? '?'}
                </Figure>
              </td>
              <td className="px-3 py-1.5 text-xs text-muted-foreground">
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
    </DataPanel>
  )
}

/** The chronological tail — every run of this document, oldest first. */
function LogTail({ progress }: { progress: IngestProgress }): ReactElement {
  return (
    <DataPanel
      as="h3"
      eyebrow="run_events · the durable record, replayed"
      title="Log"
      collapsible
      maxHeight="14rem"
      actions={
        <InfoTip label="Why the log cannot drift from the pipeline">
          The entry and the stage bump are one transaction, so neither can exist without the
          other. A refresh mid-ingest resumes this view instead of losing it.
        </InfoTip>
      }
    >
      {progress.entries.length === 0 ? (
        <Absence
          figure="Run events"
          why="no stage has committed yet"
          needed="The first entry appears the moment one does."
        />
      ) : (
        <ul className="divide-y divide-border/70">
          {progress.entries.map((entry) => (
            <li
              key={`${entry.kind}-${entry.seq}-${entry.ts}`}
              className="flex gap-3 py-1 font-mono text-xs"
            >
              <span className="tabular shrink-0 text-muted-foreground">{clock(entry.ts)}</span>
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
    </DataPanel>
  )
}
