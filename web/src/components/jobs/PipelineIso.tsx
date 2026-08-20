'use client'

import { CircleDot, Loader2, Radio, TriangleAlert } from 'lucide-react'
import {
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
  type MotionValue,
} from 'motion/react'
import { useEffect, useId, useRef, useState, type ReactElement } from 'react'

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
/**
 * How far above a full-height block the label band floats.
 *
 * The labels are pinned to a fixed altitude rather than to each block's own top,
 * so a block that grows on a poll slides *under* its label instead of pushing it
 * off the canvas — and the six labels stay a clean parallel band at every load.
 */
const LABEL_Z = H_MAX + 44

const OX = V * RX + 6
const OY = H_MAX + 52
const VIEW_W = Math.round((5 * PITCH + U + V) * RX + 14)
const VIEW_H = Math.round((5 * PITCH + U + V) * RY + OY + 16)

/** Project an axis-space point onto the isometric plane. */
function project(a: number, b: number, z: number): [number, number] {
  return [OX + (a - b) * RX, OY + (a + b) * RY - z]
}

/** `x,y x,y …` for an SVG polygon. */
function poly(points: Array<[number, number]>): string {
  return points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
}

/** The top face of the block for stage `i`, at height `h`. */
function topFace(i: number, h: number): string {
  const a0 = i * PITCH
  const a1 = a0 + U
  return poly([project(a0, 0, h), project(a1, 0, h), project(a1, V, h), project(a0, V, h)])
}

/** The right (lit) face. */
function rightFace(i: number, h: number): string {
  const a1 = i * PITCH + U
  return poly([project(a1, 0, h), project(a1, V, h), project(a1, V, 0), project(a1, 0, 0)])
}

/** The left (shaded) face. */
function leftFace(i: number, h: number): string {
  const a0 = i * PITCH
  const a1 = a0 + U
  return poly([project(a0, V, h), project(a1, V, h), project(a1, V, 0), project(a0, V, 0)])
}

/** The footprint the block casts its contact shadow onto. */
function baseFace(i: number): string {
  const a0 = i * PITCH
  const a1 = a0 + U
  return poly([project(a0, 0, 0), project(a1, 0, 0), project(a1, V, 0), project(a0, V, 0)])
}

/**
 * Follow a set of live counts and report which of them just went up.
 *
 * The block heights animate, but an animation is only legible while it is
 * running: a reader who blinks during a poll sees the *result* and cannot tell
 * that anything moved. So each increase is also stated — `+2`, beside the count
 * it changed, for four seconds — which is the same information as a number
 * rather than as motion, and is what makes the strip readable under
 * `prefers-reduced-motion` where nothing moves at all.
 *
 * Nothing here is invented: a delta is the difference between two counts that
 * both came from `job_runs`, and it is dropped rather than accumulated so a
 * stale `+2` can never outlive the poll that produced it.
 */
function useIncrease(stages: StageLoad[]): Record<string, number> {
  const signature = stages.map((s) => `${s.name}:${s.through}`).join('|')
  const previous = useRef<Map<string, number> | null>(null)
  const [delta, setDelta] = useState<Record<string, number>>({})

  useEffect(() => {
    const now = new Map<string, number>()
    for (const part of signature.split('|')) {
      const [name, count] = part.split(':')
      now.set(name, Number(count))
    }
    const before = previous.current
    previous.current = now
    // The first render has nothing to compare against. An initial load is not a
    // change, and labelling it `+327` would be a lie about what just happened.
    if (before === null) return

    const gained: Record<string, number> = {}
    let any = false
    for (const [name, count] of now) {
      const rise = count - (before.get(name) ?? count)
      if (rise > 0) {
        gained[name] = rise
        any = true
      }
    }
    if (!any) return
    setDelta(gained)
    const timer = setTimeout(() => setDelta({}), 4000)
    return () => clearTimeout(timer)
  }, [signature])

  return delta
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
 * **It is live.** `JobsView` re-reads `GET /jobs` on a timer — fast while work is
 * in flight, slow when it is not, paused when the tab is hidden — and hands the
 * new rows down. Each block's height is a spring, so a stage that gains a run
 * *rises into* its new height rather than teleporting, and the run that is
 * currently being worked sits on the block it is inside as a marker that moves.
 * DESIGN.md §6 forbids ambient loops, and neither of those is one: both are
 * conditional on real, in-progress work, and both stop the moment the queue does.
 *
 * Colour here is **quantity, not status** — one hue at three intensities, lit
 * from above, matte. A failure is a status, so it is never a face colour: it is
 * a chip with an icon and a word, floating over the block it belongs to.
 */
export function PipelineIso({
  rows,
  loading = false,
  updatedAt = null,
  polling = false,
}: {
  rows: JobRunRow[]
  loading?: boolean
  /** Epoch ms of the read these rows came from, for the live stamp. */
  updatedAt?: number | null
  /** Whether the queue is being re-read on a timer right now. */
  polling?: boolean
}): ReactElement {
  const reduce = useReducedMotion() ?? false
  const shadowId = useId()
  const { stages, counted } = foldStages(rows)
  const peak = Math.max(1, ...stages.map((s) => s.through))
  const increase = useIncrease(stages)

  const inFlight = stages.reduce((n, s) => n + s.active, 0)
  const broken = stages.reduce((n, s) => n + s.failed, 0)

  return (
    <Card>
      <CardHeader
        eyebrow="ingest pipeline · six stages, in order"
        title="Where the corpus is"
        actions={
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            <LiveStamp at={updatedAt} polling={polling} inFlight={inFlight > 0} reduce={reduce} />
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
            className="max-h-[19rem] w-full"
            role="img"
            aria-label={`Ingest funnel: ${stages
              .map(
                (s) =>
                  `${s.name} ${s.through} committed` +
                  (s.active > 0 ? `, ${s.active} in flight` : '') +
                  (s.failed > 0 ? `, ${s.failed} failed` : ''),
              )
              .join('; ')}.`}
          >
            <defs>
              <filter id={shadowId} x="-30%" y="-30%" width="160%" height="160%">
                <feGaussianBlur stdDeviation="5" />
              </filter>
            </defs>

            {/* The ground track the stages sit on, drawn first so every block
                paints over it. */}
            <polyline
              points={poly([project(-0.5, V / 2, 0), project(5 * PITCH + U + 0.5, V / 2, 0)])}
              fill="none"
              stroke="var(--blue-400)"
              strokeOpacity="0.4"
              strokeWidth="1.5"
              strokeDasharray="5 5"
            />

            {stages.map((stage, i) => (
              <StageSolid
                key={stage.name}
                index={i}
                stage={stage}
                height={H_MIN + (stage.through / peak) * (H_MAX - H_MIN)}
                shadowId={shadowId}
                reduce={reduce}
                loading={loading}
                gained={increase[stage.name] ?? 0}
              />
            ))}
          </svg>
          <Receipt
            label="Height"
            origin="job_runs.completed_stage"
            detail={`${counted} ingest runs · re-read ${polling ? 'on a timer' : 'on demand'}`}
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
                {stage.active > 0 ? (
                  <span className="inline-flex items-center gap-1 rounded-md bg-blue-400/12 px-1.5 py-0.5 font-mono text-[0.65rem] font-medium text-blue-700">
                    <Loader2
                      className="size-3 animate-spin motion-reduce:animate-none"
                      aria-hidden
                    />
                    {stage.active} in flight
                  </span>
                ) : null}
                {stage.failed > 0 ? (
                  <span className="inline-flex items-center gap-1 rounded-md bg-block/25 px-1.5 py-0.5 font-mono text-[0.65rem] font-medium text-[color:var(--block-ink)]">
                    <TriangleAlert className="size-3" aria-hidden />
                    {stage.failed} failed
                  </span>
                ) : null}
                {increase[stage.name] ? (
                  <span className="font-mono text-[0.65rem] font-medium text-blue-700">
                    +{increase[stage.name]}
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

/**
 * One stage's solid, with its height on a spring.
 *
 * The three faces are `MotionValue`-driven polygons derived from a single height
 * value, so a poll that changes a count re-targets one spring and nothing in this
 * subtree re-renders. Under `prefers-reduced-motion` the spring is jumped to its
 * target instead of run — the count still updates, it simply arrives rather than
 * travels.
 */
function StageSolid({
  index,
  stage,
  height,
  shadowId,
  reduce,
  loading,
  gained,
}: {
  index: number
  stage: StageLoad
  height: number
  shadowId: string
  reduce: boolean
  loading: boolean
  gained: number
}): ReactElement {
  const raw = useMotionValue(H_MIN)
  const h = useSpring(raw, { stiffness: 130, damping: 20, mass: 0.7 })

  useEffect(() => {
    if (reduce) h.jump(height)
    else raw.set(height)
  }, [height, reduce, raw, h])

  const top = useTransform(h, (v: number) => topFace(index, v))
  const right = useTransform(h, (v: number) => rightFace(index, v))
  const left = useTransform(h, (v: number) => leftFace(index, v))

  const [lx, ly] = project(index * PITCH + U / 2, V / 2, LABEL_Z)

  return (
    <g>
      {/* Contact shadow — the one soft key light of DESIGN.md §7, expressed as
          the shadow it would cast. */}
      <polygon
        points={baseFace(index)}
        fill="var(--blue-900)"
        opacity="0.1"
        filter={`url(#${shadowId})`}
      />
      <motion.polygon points={left} fill="var(--blue-400)" />
      <motion.polygon points={right} fill="var(--blue-600)" />
      <motion.polygon
        points={top}
        fill={stage.active > 0 ? 'var(--blue-400)' : 'var(--blue-200)'}
        stroke="var(--surface)"
        strokeOpacity="0.55"
        strokeWidth="1"
      />

      {stage.active > 0 ? (
        <WorkMarker index={index} height={h} reduce={reduce} count={stage.active} />
      ) : null}

      <text x={lx} y={ly} textAnchor="middle" className="fill-muted-foreground font-mono text-[10px]">
        {stage.name}
      </text>
      <text
        x={lx}
        y={ly + 14}
        textAnchor="middle"
        className="fill-foreground font-mono text-[12px] font-semibold"
      >
        {loading ? '·' : stage.through}
      </text>
      {gained > 0 ? (
        <motion.text
          key={`gain-${gained}`}
          x={lx + 20}
          y={ly + 14}
          textAnchor="start"
          className="fill-blue-700 font-mono text-[10px] font-semibold"
          initial={reduce ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.28, ease: 'easeOut' }}
        >
          {`+${gained}`}
        </motion.text>
      ) : null}
      {stage.failed > 0 ? <FailChip x={lx} y={ly + 30} count={stage.failed} /> : null}
    </g>
  )
}

/**
 * The run that is being worked, sitting on the block it is inside.
 *
 * A marker on the top face, pinned to the spring so it rides the block up as the
 * stage grows, with a ring that expands out of it once a second. The loop exists
 * only while `job_runs` actually holds an in-flight run at this stage, so it is
 * work being shown rather than the ambient animation DESIGN.md §6 rules out — and
 * it is `aria-hidden`, because the same fact is a spinner, a figure and the word
 * "in flight" in the roster beside it.
 */
function WorkMarker({
  index,
  height,
  reduce,
  count,
}: {
  index: number
  height: MotionValue<number>
  reduce: boolean
  count: number
}): ReactElement {
  const cx = project(index * PITCH + U / 2, V / 2, 0)[0]
  // The marker rides the same spring as the block, as a translate on the group,
  // so the ring inside it can scale about its own centre without inheriting the
  // block's height into its transform origin.
  const ty = useTransform(height, (v: number) => project(index * PITCH + U / 2, V / 2, v)[1] - 5)

  return (
    <motion.g aria-hidden style={{ y: ty }}>
      {reduce ? null : (
        <motion.circle
          cx={cx}
          cy={0}
          r={4.5}
          fill="none"
          stroke="var(--blue-600)"
          strokeWidth={1.4}
          animate={{ scale: [1, 2.6], opacity: [0.7, 0] }}
          transition={{ duration: 1.5, repeat: Infinity, ease: 'easeOut' }}
        />
      )}
      <circle cx={cx} cy={0} r={3.4} fill="var(--blue-900)" />
      {count > 1 ? (
        <text x={cx + 7} y={0} dy="3" className="fill-blue-700 font-mono text-[9px] font-semibold">
          {`×${count}`}
        </text>
      ) : null}
    </motion.g>
  )
}

/**
 * A stage's failures, as a chip with an icon and a word.
 *
 * DESIGN.md §2: status is never carried by a fill on its own, and the block faces
 * are quantity, not verdict. So a broken stage does not go red — a labelled chip
 * appears above it and the roster row beside it grows the same chip.
 */
function FailChip({ x, y, count }: { x: number; y: number; count: number }): ReactElement {
  const word = `${count} failed`
  const width = 24 + word.length * 5.6
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect
        x={-width / 2}
        y={-8}
        width={width}
        height={16}
        rx={8}
        fill="var(--block)"
        fillOpacity="0.22"
        stroke="var(--block)"
        strokeOpacity="0.55"
        strokeWidth="0.75"
      />
      <g transform={`translate(${-width / 2 + 9} 0)`}>
        <path d="M0 -4.6 L4.6 3.4 L-4.6 3.4 Z" fill="var(--block-ink)" />
        <rect x="-0.55" y="-2.2" width="1.1" height="3" rx="0.55" fill="var(--surface)" />
        <rect x="-0.55" y="1.5" width="1.1" height="1.1" rx="0.55" fill="var(--surface)" />
      </g>
      <text
        x={-width / 2 + 17}
        y="0"
        dy="3.2"
        className="font-mono text-[9.5px] font-medium"
        fill="var(--block-ink)"
      >
        {word}
      </text>
    </g>
  )
}

/**
 * When the strip last agreed with the database, and whether it is still asking.
 *
 * A live surface has to say that it is live, or a reader cannot tell a quiet
 * queue from a frozen page. The stamp is the wall-clock time of the last
 * successful `GET /jobs`, never a synthetic "now".
 */
function LiveStamp({
  at,
  polling,
  inFlight,
  reduce,
}: {
  at: number | null
  polling: boolean
  inFlight: boolean
  reduce: boolean
}): ReactElement | null {
  if (at === null) return null
  const stamp = new Date(at).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2 py-0.5 text-xs text-muted-foreground">
      <Radio
        aria-hidden
        className={cn(
          'size-3 text-blue-700',
          polling && inFlight && !reduce && 'animate-pulse motion-reduce:animate-none',
        )}
      />
      {polling ? 'live' : 'paused'} · <span className="tabular font-mono">{stamp}</span>
    </span>
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
      className={cn(
        'size-3.5 shrink-0',
        stage.through > 0 ? 'text-blue-600' : 'text-muted-foreground/50',
      )}
      aria-hidden
    />
  )
}

/** A compact count in the card header — a dot, a figure, a word. */
function Beacon({ tone, label }: { tone: 'ok' | 'agent' | 'block'; label: string }): ReactElement {
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
