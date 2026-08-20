'use client'

import { ShieldCheck } from 'lucide-react'
import type { CSSProperties, ReactElement } from 'react'

import { InfoTip } from '@/components/primitives/InfoTip'
import { Badge } from '@/components/ui/Badge'
import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'

import {
  RESIDUAL_META,
  residualSignal,
  type Residual,
  type RiskMovement,
} from './riskRanking'

/**
 * The Risk Map chart: one **dumbbell** per risk — where the risk sat with no
 * control (hollow mark), where the real Aegis control leaves it (filled mark),
 * and the arrow between them.
 *
 * Why this shape. The page answers one question: *how much has Aegis reduced my
 * risk?* A 5×5 matrix (v1) plotted six lonely pills in twenty-five cells and ran
 * two colour scales at once. A ranked ladder (v2) showed the before as bar length
 * and the after as bar colour — two separate encodings that never drew the
 * movement between them, which is the entire story. A dumbbell draws the movement
 * itself: every row starts on the right and is pushed left toward zero, so the
 * gestalt of the whole chart *is* the reduction.
 *
 * Encodings, and there are only two:
 *  - **position on a shared 0..ceiling axis** — exposure (likelihood × impact).
 *    Left is safer. The distance travelled is what Aegis removed.
 *  - **colour of the filled mark** — the residual band still carried.
 *
 * The hollow "before" mark and the connector are deliberately achromatic: they are
 * where you started, not a second severity scale. Colour means one thing here.
 */

/** Neutral ink for the rail, connector and "before" mark (mirrors `--muted-foreground`). */
const TRACK_INK = '#98a2b3'

/**
 * Shared column template so the axis header's ticks line up exactly with the
 * tracks below it. Identity · track · before→after · residual band.
 */
const ROW_GRID =
  'grid grid-cols-1 gap-x-5 gap-y-2 md:grid-cols-[minmax(0,19rem)_minmax(0,1fr)_5rem_6.5rem]'

/** Axis tick positions as a share of the ceiling — 0, 20, 40, 60, 80, 100 %. */
const TICKS = [0, 0.2, 0.4, 0.6, 0.8, 1] as const

/** Residual band → Badge variant (green / amber / red) — the single colour scale. */
const RESIDUAL_BADGE: Record<Residual, 'ok' | 'risk' | 'block'> = {
  low: 'ok',
  medium: 'risk',
  high: 'block',
}

interface RiskDumbbellProps {
  /** Risks already turned into movements, largest reduction first. */
  moves: RiskMovement[]
  /** Worst exposure the published scale allows — the shared axis ceiling. */
  ceiling: number
}

export function RiskDumbbell({ moves, ceiling }: RiskDumbbellProps): ReactElement {
  return (
    <div>
      <AxisHeader ceiling={ceiling} />
      <ol className="divide-y divide-border">
        {moves.map((move) => (
          <Row key={move.risk.id} move={move} ceiling={ceiling} />
        ))}
      </ol>
    </div>
  )
}

/**
 * The shared axis, drawn once above the rows. Ticks are derived from the
 * response's own scale, so the numbers on the rows and the numbers on the axis
 * can never disagree.
 */
function AxisHeader({ ceiling }: { ceiling: number }): ReactElement {
  return (
    <div className={cn(ROW_GRID, 'hidden items-end px-5 pb-1.5 md:grid')}>
      <span className="eyebrow">Risk &amp; the control on it</span>
      <div className="relative h-4">
        {TICKS.map((t) => (
          <span
            key={t}
            className="absolute bottom-0 -translate-x-1/2 font-mono text-[0.6rem] text-muted-foreground"
            style={{ left: `${t * 100}%` }}
          >
            {Math.round(t * ceiling)}
          </span>
        ))}
      </div>
      <span className="eyebrow text-right">Before → after</span>
      <span className="eyebrow text-right">Still carried</span>
    </div>
  )
}

/** One risk: its dumbbell, its two numbers, and the control that moved it. */
function Row({ move, ceiling }: { move: RiskMovement; ceiling: number }): ReactElement {
  const { risk, inherent, residual, removedPct } = move
  const meta = RESIDUAL_META[risk.residual]
  const hue = SIGNALS[residualSignal(risk.residual)].hex

  return (
    <li className={cn(ROW_GRID, 'px-5 py-3.5 transition-colors duration-150 hover:bg-surface-2/50')}>
      {/* Identity — id, name, OWASP-Agentic category. */}
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 md:self-center">
        <span className="font-mono text-[0.68rem] tabular text-muted-foreground">{risk.id}</span>
        <span className="t-title text-foreground">{risk.title}</span>
        <span className="eyebrow">{risk.category}</span>
      </div>

      {/* The move itself. */}
      <Track move={move} ceiling={ceiling} hue={hue} />

      {/* The same move as digits, so the chart is auditable, not just pretty. */}
      <div className="flex items-center gap-2 md:flex-col md:items-end md:justify-center md:gap-0.5">
        <span className="tabular font-mono text-[0.82rem] text-foreground">
          {inherent} <span className="text-muted-foreground">→</span>{' '}
          <span className="font-semibold">{residual}</span>
        </span>
        <span className="tabular font-mono text-[0.66rem] text-muted-foreground">
          −{Math.round(removedPct)}%
        </span>
      </div>

      {/* What is still carried — the one place colour is allowed to mean something. */}
      <div className="flex items-center md:justify-end">
        <Badge tone={RESIDUAL_BADGE[risk.residual]}>
          <span className="size-1.5 rounded-full" style={{ background: hue }} aria-hidden />
          {meta.label}
        </Badge>
      </div>

      {/* The control that did the moving. Two audiences, in priority order: the client
          reads the named control; the code pointer beside it is auditor provenance, set
          small and monospaced so it reads as a citation.

          The mitigation sentence used to sit between them at reading size, once per
          risk — nine paragraphs down one screen, which is the wall DESIGN.md §9 names.
          It explains a *mechanism*, and §4 is explicit that a mechanism belongs in an
          InfoTip. Nothing is lost: it is one hover from the control it describes, and
          the control's own name and reference still read at a glance. */}
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 md:col-span-4">
        <p className="flex items-center gap-1.5 text-[0.82rem] font-medium text-blue-700">
          <ShieldCheck className="size-3.5 shrink-0" aria-hidden />
          {risk.control_name}
          <InfoTip label={`How ${risk.control_name} holds ${risk.title} down`}>
            {risk.mitigation}
          </InfoTip>
        </p>
        <p className="min-w-0 truncate font-mono text-[0.68rem] text-muted-foreground">
          {risk.control_ref}
        </p>
      </div>
    </li>
  )
}

/**
 * The dumbbell: a hollow mark at the inherent position, a filled band-coloured
 * mark at the residual position, and an arrow along the distance the control
 * removed. Everything is positioned as a percentage of the shared ceiling, so
 * rows are directly comparable to each other and to the headline above.
 */
function Track({
  move,
  ceiling,
  hue,
}: {
  move: RiskMovement
  ceiling: number
  hue: string
}): ReactElement {
  const { risk, inherent, residual, inherentPos, residualPos, removedPct } = move
  const span = Math.max(0, inherentPos - residualPos)
  const meta = RESIDUAL_META[risk.residual]

  return (
    <div
      className="relative h-7 md:self-center"
      role="img"
      aria-label={`${risk.title}: exposure fell from ${inherent} to ${residual} out of ${ceiling}, a ${Math.round(removedPct)} percent reduction, leaving ${meta.label.toLowerCase()} residual risk.`}
    >
      {/* The 0..ceiling domain, so an unmoved risk would still read against a scale. */}
      <span
        className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2"
        style={{ background: 'var(--border)' }}
        aria-hidden
      />

      {/* What the control removed — the distance travelled. */}
      <span
        className="absolute top-1/2 h-[7px] -translate-y-1/2 rounded-full"
        style={{
          left: `${residualPos}%`,
          width: `${span}%`,
          background: `${TRACK_INK}59`,
        }}
        aria-hidden
      />

      {/* Direction: the arrow points at zero, because that is where we pushed it. */}
      {span > 4 && <Arrowhead left={residualPos} />}

      {/* Before: where the risk sits with nothing in its way. */}
      <span
        className="absolute top-1/2 size-[11px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-card"
        style={{ left: `${inherentPos}%`, border: `1.5px solid ${TRACK_INK}` }}
        aria-hidden
      />

      {/* After: where the Aegis control leaves it. Colour = the band still carried. */}
      <span
        className="absolute top-1/2 size-[13px] -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-card"
        style={{ left: `${residualPos}%`, background: hue }}
        aria-hidden
      />
    </div>
  )
}

/** A small left-pointing arrowhead sitting just ahead of the "after" mark. */
function Arrowhead({ left }: { left: number }): ReactElement {
  const style: CSSProperties = {
    left: `calc(${left}% + 12px)`,
    borderTop: '5px solid transparent',
    borderBottom: '5px solid transparent',
    borderRight: `6px solid ${TRACK_INK}`,
  }
  return (
    <span
      className="absolute top-1/2 size-0 -translate-y-1/2"
      style={style}
      aria-hidden
    />
  )
}
