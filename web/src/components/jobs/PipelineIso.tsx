'use client'

import { CircleDot, Loader2, TriangleAlert } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { useId, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import type { JobRunRow } from '@/lib/api/jobs'
import { cn } from '@/lib/utils'

/**
 * The six ingest stages, in the order `aegis.jobs.stages.INGEST_STAGES` declares
 * them, with the queue each one is pinned to and the reason it is pinned there.
 *
 * The prose here is not on the page: it is the body of one {@link InfoTip} per
 * stage. DESIGN.md §4 — a paragraph that explains a *mechanism* is a tooltip.
 */
const STAGES = [
  {
    name: 'parse',
    queue: 'cpu',
    tip: 'Docling reads the PDF and scores its own reading order. CPU- and RAM-bound, so it serialises on the cpu queue. 30 min timeout, 2 attempts — a parse that fails twice is a document, not a flake.',
  },
  {
    name: 'chunk',
    queue: 'default',
    tip: 'Splits the parsed text on structure, lifting tables out as their own chunks so a table keeps its shape. Cheap record-layer work: default queue, 5 min, 3 attempts.',
  },
  {
    name: 'enrich',
    queue: 'default',
    tip: 'Attaches the metadata a retrieval filter needs — doc type, date, section path. Default queue, 5 min, 3 attempts.',
  },
  {
    name: 'embed',
    queue: 'io',
    tip: 'The one billed network stage, so it runs wide on the io queue rather than serialising. 15 min, 5 attempts — a provider 429 is expected, not exceptional.',
  },
  {
    name: 'index',
    queue: 'default',
    tip: 'Writes the vectors into this tenant’s own collection. Default queue, 10 min, 3 attempts.',
  },
  {
    name: 'graph',
    queue: 'cpu',
    tip: 'Extracts entities and relations onto chunks.meta. CPU-bound like parse, so it shares the cpu queue. 30 min, 2 attempts.',
  },
] as const

/** Statuses in which the orchestrator may still be holding a worker slot. */
const IN_FLIGHT = new Set(['pending', 'running', 'reconciling'])

/** What one stage of the pipeline is currently carrying. */
export interface StageLoad {
  name: string
  queue: string
  tip: string
  /** Runs that committed this stage — the funnel depth, and the block's height. */
  through: number
  /** Runs still in flight whose next stage is this one. */
  active: number
  /** Runs that died here: they committed the stage before and never this one. */
  failed: number
}

/**
 * Fold the job rows into the six-stage funnel.
 *
 * Everything here is arithmetic over `job_runs.completed_stage`, which is the
 * column the substrate bumps inside each stage's own transaction. A run whose
 * `completed_stage` is `embed` provably committed `parse`, `chunk`, `enrich` and
 * `embed`, so it counts toward all four — and a run that never wrote a stage
 * counts toward none of them rather than being guessed into the first.
 */
export function foldStages(rows: JobRunRow[]): { stages: StageLoad[]; counted: number } {
  const order: string[] = STAGES.map((s) => s.name)
  const index = (stage: string | null): number => (stage === null ? -1 : order.indexOf(stage))

  const counted = rows.filter((row) => index(row.completed_stage) >= 0).length

  const stages = STAGES.map((stage, i) => {
    let through = 0
    let active = 0
    let failed = 0
    for (const row of rows) {
      const at = index(row.completed_stage)
      // `completed_stage` names a stage this pipeline does not have — a reindex
      // or an eval run. It is not folded into an ingest funnel it never entered.
      if (row.completed_stage !== null && at < 0) continue
      if (at >= i) through += 1
      else if (at === i - 1) {
        if (IN_FLIGHT.has(row.status)) active += 1
        else if (row.status === 'failed') failed += 1
      }
    }
    return { ...stage, through, active, failed }
  })

  return { stages, counted }
}

/* ── Isometric geometry ──────────────────────────────────────────────────────
 *
 * DESIGN.md §7 decided this technique by measurement: six labelled, stateful
 * blocks are an SVG-isometric problem, not a WebGL one. WebGL costs 235 kB gzip
 * to draw six boxes and takes their labels out of the accessibility tree; every
 * label below is real text.
 *
 * One projection, applied once, in one place — the file has no nested transform
 * and therefore none of the cross-engine depth-sorting bugs that `preserve-3d`
 * stacking brings. Depth order is paint order: `a` grows toward the viewer, so
 * drawing the blocks in pipeline order is already back-to-front.
 */

/** One step along the pipeline axis, in px. Flattened from true 30° so the strip
 *  is a header band rather than a 500px staircase. */
const RX = 34
const RY = 11
/** Block footprint and centre-to-centre pitch, in axis units. */
const U = 1.9
const V = 1.9
const PITCH = 2.35
/** The tallest a block gets. The floor keeps an empty stage a block, not a line. */
const H_MAX = 54
const H_MIN = 10

const OX = V * RX + 6
const OY = H_MAX + 26
const VIEW_W = Math.round((5 * PITCH + U + V) * RX + 12)
const VIEW_H = Math.round((5 * PITCH + U + V) * RY + H_MAX + 44)

/** Project an axis-space point onto the isometric plane. */
function project(a: number, b: number, z: number): [number, number] {
  return [OX + (a - b) * RX, OY + (a + b) * RY - z]
}

/** `x,y x,y …` for an SVG polygon. */
function poly(points: Array<[number, number]>): string {
  return points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
}

/** The three visible faces of the block for stage `i`, at height `h`. */
function faces(i: number, h: number): { top: string; left: string; right: string; base: string } {
  const a0 = i * PITCH
  const a1 = a0 + U
  return {
    top: poly([project(a0, 0, h), project(a1, 0, h), project(a1, V, h), project(a0, V, h)]),
    right: poly([project(a1, 0, h), project(a1, V, h), project(a1, V, 0), project(a1, 0, 0)]),
    left: poly([project(a0, V, h), project(a1, V, h), project(a1, V, 0), project(a0, V, 0)]),
    base: poly([project(a0, 0, 0), project(a1, 0, 0), project(a1, V, 0), project(a0, V, 0)]),
  }
}

/**
 * The pipeline as six matte isometric solids — the header visual, and never how
 * you read a job's state.
 *
 * A block's **height is the number of runs that committed that stage**, so the
 * shape of the strip is the shape of the funnel: a tall `parse` beside a short
 * `graph` is a corpus that is indexed but not yet extracted, and that is legible
 * at a glance from the far side of a room. Every one of those numbers is also
 * printed as text in the roster beside it, because DESIGN.md §7 is explicit that
 * a 3D accent is never the only carrier of information.
 *
 * Colour here is **quantity, not status** — one hue at three intensities, lit
 * from above, matte. A failure is a status, so it is never a face colour: it is
 * a chip with an icon and a word, floating over the block it belongs to.
 */
export function PipelineIso({
  rows,
  loading = false,
}: {
  rows: JobRunRow[]
  loading?: boolean
}): ReactElement {
  const reduce = useReducedMotion()
  const shadowId = useId()
  const { stages, counted } = foldStages(rows)
  const peak = Math.max(1, ...stages.map((s) => s.through))
  const heights = stages.map((s) => H_MIN + (s.through / peak) * (H_MAX - H_MIN))

  const inFlight = stages.reduce((n, s) => n + s.active, 0)
  const broken = stages.reduce((n, s) => n + s.failed, 0)

  return (
    <Card>
      <CardHeader
        eyebrow="ingest pipeline · six stages, in order"
        title="Where the corpus is"
        actions={
          <div className="flex items-center gap-1.5">
            <Beacon tone="ok" label={`${counted} committed`} />
            {inFlight > 0 ? <Beacon tone="agent" label={`${inFlight} in flight`} /> : null}
            {broken > 0 ? <Beacon tone="block" label={`${broken} failed`} /> : null}
          </div>
        }
      />
      <CardBody className="grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:gap-6">
        <div className="min-w-0">
          <svg
            viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
            className="w-full"
            role="img"
            aria-label={`Ingest funnel: ${stages
              .map((s) => `${s.name} ${s.through}`)
              .join(', ')} runs committed.`}
          >
            <defs>
              <filter id={shadowId} x="-30%" y="-30%" width="160%" height="160%">
                <feGaussianBlur stdDeviation="5" />
              </filter>
            </defs>

            {/* The ground track the stages sit on, drawn first so every block
                paints over it. */}
            <polyline
              points={poly([
                project(-0.5, V / 2, 0),
                project(5 * PITCH + U + 0.5, V / 2, 0),
              ])}
              fill="none"
              stroke="var(--blue-400)"
              strokeOpacity="0.4"
              strokeWidth="1.5"
              strokeDasharray="5 5"
            />

            {stages.map((stage, i) => {
              const h = heights[i]
              const f = faces(i, h)
              const [lx, ly] = project(i * PITCH + U / 2, V / 2, H_MAX + 20)
              return (
                <motion.g
                  key={stage.name}
                  initial={reduce ? false : { opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.32, delay: reduce ? 0 : i * 0.06, ease: 'easeOut' }}
                >
                  {/* Contact shadow — the one soft key light of DESIGN.md §7,
                      expressed as the shadow it would cast. */}
                  <polygon
                    points={f.base}
                    fill="var(--blue-900)"
                    opacity="0.1"
                    filter={`url(#${shadowId})`}
                  />
                  <polygon points={f.left} fill="var(--blue-400)" />
                  <polygon points={f.right} fill="var(--blue-600)" />
                  <polygon
                    points={f.top}
                    fill={stage.active > 0 ? 'var(--blue-400)' : 'var(--blue-200)'}
                    stroke="var(--surface)"
                    strokeOpacity="0.55"
                    strokeWidth="1"
                  />
                  <text
                    x={lx}
                    y={ly}
                    textAnchor="middle"
                    className="fill-muted-foreground font-mono text-[10px]"
                  >
                    {stage.name}
                  </text>
                  <text
                    x={lx}
                    y={ly + 13}
                    textAnchor="middle"
                    className="fill-foreground font-mono text-[12px] font-semibold"
                  >
                    {loading ? '·' : stage.through}
                  </text>
                  {stage.failed > 0 ? (
                    <g transform={`translate(${lx + 28} ${ly + 9})`}>
                      <circle r="7" fill="var(--block)" />
                      <text
                        textAnchor="middle"
                        y="3"
                        className="font-mono text-[9px] font-semibold"
                        fill="var(--block-ink)"
                      >
                        {stage.failed}
                      </text>
                    </g>
                  ) : null}
                </motion.g>
              )
            })}
          </svg>
          <Receipt
            label="Height"
            origin="job_runs.completed_stage"
            detail={`${counted} ingest runs`}
            className="mt-1"
          />
        </div>

        {/* The same six facts as rows. The 3D is the header visual; this is how
            you read a stage. */}
        <ol className="flex min-w-0 flex-col justify-center divide-y divide-border rounded-lg border border-border">
          {stages.map((stage) => (
            <li
              key={stage.name}
              className="flex items-center gap-2.5 px-3 py-2 transition-colors duration-[--dur-fast] hover:bg-surface-2/70"
            >
              <StageMark stage={stage} />
              <span className="font-mono text-sm font-medium text-foreground">{stage.name}</span>
              <span className="rounded-full border border-border bg-surface-2 px-1.5 py-px font-mono text-[0.65rem] text-muted-foreground">
                {stage.queue}
              </span>
              <InfoTip label={`What the ${stage.name} stage does`}>{stage.tip}</InfoTip>
              <span className="ml-auto flex shrink-0 items-center gap-2">
                {stage.failed > 0 ? (
                  <span className="inline-flex items-center gap-1 rounded-md bg-block/25 px-1.5 py-0.5 font-mono text-[0.65rem] font-medium text-[color:var(--block-ink)]">
                    <TriangleAlert className="size-3" aria-hidden />
                    {stage.failed} failed
                  </span>
                ) : null}
                <Figure className="text-foreground">{stage.through}</Figure>
              </span>
            </li>
          ))}
        </ol>
      </CardBody>
    </Card>
  )
}

/** The state marker for one stage row — icon and word, never colour alone. */
function StageMark({ stage }: { stage: StageLoad }): ReactElement {
  if (stage.active > 0) {
    return (
      <Loader2
        className="size-3.5 shrink-0 animate-spin text-blue-700 motion-reduce:animate-none"
        aria-label={`${stage.active} in flight`}
      />
    )
  }
  if (stage.failed > 0) {
    return (
      <TriangleAlert
        className="size-3.5 shrink-0 text-[color:var(--block-ink)]"
        aria-label={`${stage.failed} failed here`}
      />
    )
  }
  return (
    <CircleDot
      className={cn('size-3.5 shrink-0', stage.through > 0 ? 'text-blue-600' : 'text-muted-foreground/50')}
      aria-hidden
    />
  )
}

/** A compact count in the card header — a dot, a figure, a word. */
function Beacon({
  tone,
  label,
}: {
  tone: 'ok' | 'agent' | 'block'
  label: string
}): ReactElement {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2 py-0.5 text-xs text-muted-foreground">
      <span
        aria-hidden
        className={cn(
          'size-1.5 rounded-full',
          tone === 'ok' && 'bg-ok',
          tone === 'agent' && 'bg-blue-600',
          tone === 'block' && 'bg-block',
        )}
      />
      {label}
    </span>
  )
}
