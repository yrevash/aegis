'use client'

import { BookOpen, FileText, Layers, ScrollText } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { BarChart } from '@/components/charts/BarChart'
import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { rampHex } from '@/components/charts/palette'
import { RankedBars, type RankedDatum } from '@/components/charts/RankedBars'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { StatCard } from '@/components/ui/StatCard'
import { Scene, SceneState } from '@/components/illustration/Scene'
import { CorpusPanel } from '@/components/jobs/CorpusPanel'
import { IngestLog } from '@/components/jobs/IngestLog'
import { UploadPanel } from '@/components/jobs/UploadPanel'
import { getDocuments, type DocumentRow } from '@/lib/api/jobs'
import { useAuth } from '@/lib/auth/AuthContext'

/**
 * Documents — what this agent knows, and how a tenant gives it more.
 *
 * **Why this section exists.** The upload control was real and it was mounted inside
 * `Jobs`, which is a pipeline-*operations* screen on two admin portals. So the one act
 * a tenant performs more than any other — hand the agent a document — lived behind a
 * nav entry named after the queue that processes it, and the `client` portal, which is
 * the role the product exists for, had no route to it at all. A tenant that cannot
 * contribute knowledge has a read-only product.
 *
 * Everything here is composed from panels that already existed and are imported, not
 * copied: {@link UploadPanel} is the front door, {@link CorpusPanel} is the shelf, and
 * {@link IngestLog} is the per-document stage log. What this file adds is the thing
 * none of them could add on their own — the *shape of the corpus*, read from the same
 * `GET /documents` projection the shelf reads, so "what does it know?" is answered
 * before "what did I upload?".
 *
 * **The counts are honest about the placeholders.** Six of this tenant's rows are
 * 0-chunk seed placeholders sitting at `pending`; they are counted as pending and
 * never as knowledge, and the searchable figure is the sum of real chunk counts —
 * which is `null` on every row that has not parsed, and is therefore stated as an
 * {@link Absence} when nothing has.
 *
 * **What is drawn, and what is refused.** {@link corpusMarks} folds the rows into four
 * marks and every one of them is a count of rows or a row's own column: status
 * composition, `chunk_count` per document, `parse_confidence` in five fixed bands, and
 * arrivals per day from `created_at`. That last one is a **volume, not a metric trend** —
 * it says how many documents landed, never whether ingest got better — which is why the
 * rule against a `StatCard trend` on this screen still stands. A row with no
 * `parse_confidence` or no `created_at` is tallied as unmeasured rather than dropped into
 * the first bucket, because a null is not a zero.
 */

/** The one focus treatment on this screen. */
const FOCUS =
  'outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background'

/** What the corpus adds up to. Derived, never asserted — every field comes off a row. */
interface CorpusShape {
  total: number
  ingested: number
  pending: number
  failed: number
  /** Summed `chunk_count`, or null when not one document has parsed. */
  chunks: number | null
  /** Distinct `doc_type` values the tenant supplied. */
  types: string[]
  /** How many rows carry no `doc_type` at all. */
  untyped: number
}

/** Fold the document rows into the shape above. */
export function summarizeCorpus(rows: readonly DocumentRow[]): CorpusShape {
  let chunks: number | null = null
  const types = new Set<string>()
  let untyped = 0
  for (const row of rows) {
    if (row.chunk_count != null) chunks = (chunks ?? 0) + row.chunk_count
    if (row.doc_type) types.add(row.doc_type)
    else untyped += 1
  }
  return {
    total: rows.length,
    ingested: rows.filter((r) => r.status === 'succeeded').length,
    pending: rows.filter((r) => r.status === 'pending' || r.status === 'running').length,
    failed: rows.filter((r) => r.status === 'failed').length,
    chunks,
    types: [...types].sort(),
    untyped,
  }
}

/** Arrival ticks, in the reader's own locale — one formatter, never one per row. */
const DAY_FORMAT = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

/** The five confidence bands, low → high. Fixed edges, so the axis means one thing. */
const CONFIDENCE_BANDS = ['0–20%', '20–40%', '40–60%', '60–80%', '80–100%'] as const

/** Everything this screen can honestly draw off the rows, derived in one pass. */
export interface CorpusMarks {
  /** Documents per `status`, biggest first, folded to the ordinal ramp's ceiling. */
  status: RankedDatum[]
  /** `chunk_count` per document, for the rows that carry one. */
  chunks: RankedDatum[]
  /** `parse_confidence` counted into five fixed bands. */
  confidence: { band: string; documents: number }[]
  /** How many rows have a parse score at all — the rest is a stated absence. */
  scored: number
  /** Arrivals per calendar day from `created_at`, oldest first. */
  arrivals: { day: string; documents: number }[]
  /** Rows with no `created_at`: counted, never placed on the day axis. */
  undated: number
}

/**
 * Fold the rows into every mark this screen draws.
 *
 * Four of these were computed nowhere and four more were computed and thrown
 * away. Nothing here is a new claim: each value is a count of rows, or a row's
 * own column, and a row that carries no value is counted as *unmeasured* rather
 * than as a zero — `parse_confidence` is `null` before a parse runs, and
 * `created_at` is nullable, so both have an explicit "not recorded" tally instead
 * of quietly landing in the first bucket.
 */
export function corpusMarks(rows: readonly DocumentRow[]): CorpusMarks {
  const status = new Map<string, number>()
  const chunks: RankedDatum[] = []
  const bands = new Array<number>(CONFIDENCE_BANDS.length).fill(0)
  const days = new Map<number, number>()
  const seen = new Map<string, number>()
  let scored = 0
  let undated = 0

  for (const row of rows) {
    status.set(row.status, (status.get(row.status) ?? 0) + 1)

    if (row.chunk_count != null) {
      // Two documents may share a filename; the id disambiguates so the ranked
      // list never collapses two real rows into one bar.
      const base = row.title ?? row.filename
      const count = (seen.get(base) ?? 0) + 1
      seen.set(base, count)
      chunks.push({ name: count > 1 ? `${base} #${row.document_id}` : base, value: row.chunk_count })
    }

    if (row.parse_confidence != null && Number.isFinite(row.parse_confidence)) {
      const clamped = Math.max(0, Math.min(1, row.parse_confidence))
      const index = Math.min(CONFIDENCE_BANDS.length - 1, Math.floor(clamped * CONFIDENCE_BANDS.length))
      bands[index] += 1
      scored += 1
    }

    if (row.created_at == null) {
      undated += 1
    } else {
      const at = new Date(row.created_at)
      if (Number.isNaN(at.getTime())) {
        undated += 1
      } else {
        const key = new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime()
        days.set(key, (days.get(key) ?? 0) + 1)
      }
    }
  }

  return {
    status: [...status.entries()]
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value),
    chunks,
    confidence: CONFIDENCE_BANDS.map((band, i) => ({ band, documents: bands[i] })),
    scored,
    arrivals: [...days.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([key, documents]) => ({ day: DAY_FORMAT.format(new Date(key)), documents })),
    undated,
  }
}

/** Fold a ranked list onto the four-step ordinal ramp, naming the tail. */
function slices(ranked: readonly RankedDatum[]): DonutDatum[] {
  const folded =
    ranked.length <= 4
      ? [...ranked]
      : [
          ...ranked.slice(0, 3),
          {
            name: `${ranked.length - 3} others`,
            value: ranked.slice(3).reduce((sum, d) => sum + d.value, 0),
          },
        ]
  return folded.map((d, i) => ({
    name: d.name,
    value: d.value,
    color: 'graph' as const,
    hex: rampHex(i, folded.length),
  }))
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: DocumentRow[] }

/**
 * The corpus at a glance, above the shelf.
 *
 * No `trend` on any tile: `GET /documents` is a snapshot of the table and carries no
 * history at all, so a sparkline here would be a series this screen invented
 * (DESIGN.md §3 — `trend` only where a real one exists).
 */
function CorpusShapeRow({ shape }: { shape: CorpusShape }): ReactElement {
  return (
    <div className="grid grid-cols-2 items-start gap-4 lg:grid-cols-4 [&>*]:min-w-0">
      <StatCard
        label="Documents"
        value={String(shape.total)}
        icon={FileText}
        tone="neutral"
        source="GET /documents · this tenant's own rows"
      />
      <StatCard
        label="Ingested and searchable"
        value={`${shape.ingested} of ${shape.total}`}
        icon={BookOpen}
        tone={shape.ingested > 0 ? 'ok' : 'neutral'}
        source="documents.status = succeeded"
      />
      <StatCard
        label="Waiting to be ingested"
        value={String(shape.pending)}
        icon={Layers}
        tone={shape.pending > 0 ? 'risk' : 'ok'}
        source="documents.status = pending or running"
      />
      {shape.chunks == null ? (
        <Card>
          <CardBody>
            <Absence
              figure="Searchable passages"
              why="Not one document has finished parsing, so no row carries a chunk count yet."
              needed="an ingest that reaches the chunk stage — the count is written with it"
            />
          </CardBody>
        </Card>
      ) : (
        <StatCard
          label="Searchable passages"
          value={String(shape.chunks)}
          icon={ScrollText}
          tone="agent"
          source="sum of documents.chunk_count · null on anything unparsed"
        />
      )}
    </div>
  )
}

/**
 * What the corpus is made of, beside the act that adds to it.
 *
 * This column was a full-height card carrying one 68-character sentence and a
 * picture, `hidden lg:block` — so on the widest screens it was the emptiest
 * thing on the page, and on the narrowest it was nothing at all. It now holds
 * the corpus's status composition and the `doc_type` set, both of which were
 * computed and discarded: `shape.types` and `shape.untyped` existed in
 * {@link CorpusShape} and reached no pixel.
 *
 * With an empty corpus there is genuinely nothing to compose, so the scene and
 * the one sentence are what belongs there — and only there.
 */
function CorpusMakeup({ shape, marks }: { shape: CorpusShape; marks: CorpusMarks }): ReactElement {
  if (shape.total === 0) {
    return (
      <Card className="overflow-hidden">
        <CardBody className="flex h-full flex-col items-center justify-center gap-3">
          <Scene name="upload" size="md" />
          <p className="text-center text-xs text-muted-foreground">
            A document becomes searchable only after every ingest stage commits.
            <InfoTip label="What ingest does to a document">
              Six stages — parse, chunk, enrich, embed, index, graph — each writing its own
              row; it becomes answerable when the index stage commits.
            </InfoTip>
          </p>
        </CardBody>
      </Card>
    )
  }

  return (
    <Card className="flex min-w-0 flex-col">
      <CardHeader
        eyebrow="documents · status"
        title="What the corpus is made of"
        actions={
          <InfoTip label="What ingest does to a document">
            Six stages — parse, chunk, enrich, embed, index, graph — each writing its own row;
            it becomes answerable when the index stage commits.
          </InfoTip>
        }
      />
      <CardBody className="flex min-w-0 flex-1 flex-col gap-3">
        <DonutChart
          data={slices(marks.status)}
          centerLabel={String(shape.total)}
          centerSub="documents"
          height={172}
        />
        <p className="flex flex-wrap items-center gap-1.5">
          {marks.status.length === 0 ? null : <span className="eyebrow mr-1">types</span>}
          {shape.types.length === 0 ? (
            <span className="text-xs text-muted-foreground">none declared</span>
          ) : (
            shape.types.map((type) => (
              <Badge key={type} tone="neutral" className="min-w-0 font-mono">
                <span className="truncate">{type}</span>
              </Badge>
            ))
          )}
          {shape.untyped === 0 ? null : (
            <span className="text-xs text-muted-foreground">
              <Figure>{shape.untyped}</Figure> untyped
            </span>
          )}
        </p>
        <Receipt
          className="mt-auto"
          origin="documents.status and documents.doc_type"
          detail={`${shape.failed} failed · ${shape.pending} still queued`}
        />
      </CardBody>
    </Card>
  )
}

/**
 * The three honest marks the row columns support, in one row.
 *
 * **None of them is a metric trend.** `GET /documents` is a snapshot of the
 * table; the only time column on it is `created_at`, and a count of arrivals per
 * day is a *volume* — it says nothing about whether ingest got better. The
 * file's own rule against a `StatCard trend` here is unchanged by drawing it.
 */
function CorpusQuality({ marks }: { marks: CorpusMarks }): ReactElement {
  const chunked = marks.chunks.reduce((sum, d) => sum + d.value, 0)
  const arrivalDays = marks.arrivals.length
  const busiest = marks.arrivals.reduce(
    (best, d) => (best == null || d.documents > best.documents ? d : best),
    null as { day: string; documents: number } | null,
  )

  return (
    <div className="grid min-w-0 items-start gap-4 md:grid-cols-2 xl:grid-cols-3 [&>*]:min-w-0">
      {/* ── Searchable passages per document ──────────────────────────────── */}
      <Card className="flex min-w-0 flex-col">
        <CardHeader
          eyebrow="documents · chunk_count"
          title="Searchable passages per document"
          actions={
            <Badge tone="neutral" className="font-mono">
              {marks.chunks.length} parsed
            </Badge>
          }
        />
        <CardBody className="flex min-w-0 flex-1 flex-col gap-3">
          {marks.chunks.length === 0 ? (
            <Absence
              className="text-left"
              figure="Passages per document"
              why="No document has reached the chunk stage, so no row carries a count."
              needed="an ingest that commits its chunk stage"
            />
          ) : (
            <RankedBars
              data={marks.chunks}
              valueFormatter={(v) => String(v)}
              maxRows={6}
              label="Searchable passages per document, most first"
            />
          )}
          <Receipt
            className="mt-auto"
            origin="documents.chunk_count · null before a parse commits"
            detail={`${chunked} passages across ${marks.chunks.length} documents`}
          />
        </CardBody>
      </Card>

      {/* ── Parse confidence ──────────────────────────────────────────────── */}
      <Card className="flex min-w-0 flex-col">
        <CardHeader
          eyebrow="documents · parse_confidence"
          title="How well the parser read them"
          actions={
            <Badge tone="neutral" className="font-mono">
              {marks.scored} scored
            </Badge>
          }
        />
        <CardBody className="flex min-w-0 flex-1 flex-col gap-3">
          {marks.scored === 0 ? (
            <Absence
              className="text-left"
              figure="Parse confidence"
              why="Not one document carries a parser score, so there is no distribution to draw."
              needed="a parse stage that commits — it writes its own confidence with the pages"
            />
          ) : (
            <BarChart
              data={marks.confidence}
              index="band"
              category="documents"
              color="graph"
              height={188}
              allowDecimals={false}
              valueFormatter={(v) => `${v} documents`}
            />
          )}
          <Receipt
            className="mt-auto"
            origin="documents.parse_confidence · the parser's own score in [0, 1]"
            detail={`${marks.scored} of the corpus scored`}
          />
        </CardBody>
      </Card>

      {/* ── Arrivals ──────────────────────────────────────────────────────── */}
      <Card className="flex min-w-0 flex-col">
        <CardHeader
          eyebrow="documents · created_at"
          title="When they arrived"
          actions={
            <Badge tone="neutral" className="font-mono">
              {arrivalDays} {arrivalDays === 1 ? 'day' : 'days'}
            </Badge>
          }
        />
        <CardBody className="flex min-w-0 flex-1 flex-col gap-3">
          {arrivalDays === 0 ? (
            <Absence
              className="text-left"
              figure="Arrivals by day"
              why="No row in this corpus carries an upload timestamp."
              needed="a document uploaded through this surface — the row is stamped as it is written"
            />
          ) : arrivalDays < 3 ? (
            /* One or two bars is a bar chart in name only. State the counts. */
            <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-muted-foreground">
              <Figure size="stat" className="text-foreground">
                {marks.arrivals.reduce((sum, d) => sum + d.documents, 0)}
              </Figure>
              <span>
                arrived across {arrivalDays} {arrivalDays === 1 ? 'day' : 'days'}
                {busiest ? ` — most on ${busiest.day}` : ''}. A third day makes this a histogram.
              </span>
            </p>
          ) : (
            <BarChart
              data={marks.arrivals}
              index="day"
              category="documents"
              color="agent"
              height={188}
              allowDecimals={false}
              valueFormatter={(v) => `${v} documents`}
            />
          )}
          <Receipt
            className="mt-auto"
            origin="documents.created_at · a count of arrivals, not a quality trend"
            detail={
              marks.undated === 0
                ? `${arrivalDays} days with an upload`
                : `${marks.undated} rows carry no timestamp, so they are counted and not placed`
            }
          />
        </CardBody>
      </Card>
    </div>
  )
}

/** The documents surface for one tenant-scoped principal. */
export function DocumentsView({ token }: { token: string | null }): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [reloadKey, setReloadKey] = useState(0)
  const [openDocument, setOpenDocument] = useState<number | null>(null)

  const refresh = useCallback(() => setReloadKey((n) => n + 1), [])

  useEffect(() => {
    let alive = true
    getDocuments(token)
      .then((data) => {
        if (alive) setLoad({ status: 'ready', rows: data.rows })
      })
      .catch((error: unknown) => {
        if (alive) {
          setLoad({
            status: 'error',
            message: error instanceof Error ? error.message : 'The corpus could not be read.',
          })
        }
      })
    return () => {
      alive = false
    }
  }, [token, reloadKey])

  const rows = load.status === 'ready' ? load.rows : null
  const shape = useMemo(() => summarizeCorpus(rows ?? []), [rows])
  const marks = useMemo(() => corpusMarks(rows ?? []), [rows])

  return (
    <div className="space-y-4">
      {load.status === 'loading' ? (
        <Card>
          <CardBody>
            <LoadingState rows={4} label="Reading the corpus…" />
          </CardBody>
        </Card>
      ) : load.status === 'error' ? (
        <Card>
          <CardBody>
            <ErrorState error={load.message} retry={refresh} />
          </CardBody>
        </Card>
      ) : (
        <>
          <CorpusShapeRow shape={shape} />

          {/*
            The upload, given the room the act deserves — it is the reason this
            section exists. `[&>*]:min-w-0` for the same reason as the pipeline
            grid: below `lg` this is one auto-sized track, and a grid item's
            default `min-width: auto` let the upload form set it 6px wider than
            the 390px viewport. `items-start` so a short makeup card does not
            stretch to the upload form's height.
          */}
          <div className="grid min-w-0 items-start gap-4 lg:grid-cols-[minmax(0,1fr)_20rem] [&>*]:min-w-0">
            <UploadPanel token={token} onUploaded={refresh} />
            <CorpusMakeup shape={shape} marks={marks} />
          </div>

          {load.rows.length === 0 ? (
            <Card>
              <CardBody>
                <SceneState name="empty" size="md">
                  <Absence
                    className="text-left"
                    figure="Everything the agent can ground an answer in"
                    why="This tenant has not put a document into the platform yet."
                    needed="upload above — the six ingest stages appear here as they commit"
                  />
                </SceneState>
              </CardBody>
            </Card>
          ) : (
            <>
              <CorpusQuality marks={marks} />
              <CorpusPanel token={token} reloadKey={reloadKey} onOpen={setOpenDocument} />
            </>
          )}

          {openDocument != null ? (
            <DataPanel
              eyebrow="ingest · stage by stage"
              title={
                <span className="flex flex-wrap items-center gap-2">
                  Document <Figure>{openDocument}</Figure>
                  <Badge tone="neutral">live log</Badge>
                </span>
              }
              actions={
                <button
                  type="button"
                  onClick={() => setOpenDocument(null)}
                  className={`rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 ${FOCUS}`}
                >
                  Close
                </button>
              }
            >
              <IngestLog documentId={openDocument} token={token} />
            </DataPanel>
          ) : null}

          <Receipt
            label="Source"
            origin="GET /documents · the documents table, not the job queue"
            detail="a document whose ingest never started owns no job row, so the queue alone could not list it"
          />
        </>
      )}
    </div>
  )
}

/** Client entry for the Documents section — gated on a reachable backend. */
export function DocumentsMount(): ReactElement {
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
        <PageHeader
          eyebrow="corpus · ingestion"
          title="Documents"
          actions={
            <InfoTip label="What this section is for">
              Everything the agent can ground an answer in, scoped to your tenant and visible
              to no other.
            </InfoTip>
          }
        />
        <DocumentsView token={session?.token ?? null} />
      </div>
    </BackendGate>
  )
}
