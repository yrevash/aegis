'use client'

import type { ReactElement } from 'react'

import { SIGNALS } from '@/config/signals'
import type { RiskEntry } from '@/lib/api/types'

import { RESIDUAL_META, RESIDUAL_ORDER, residualSignal, type RiskScale } from './riskRanking'

/**
 * The 5×5 likelihood × impact grid, drawn as **movement** rather than as position.
 *
 * `riskRanking.ts` records that a matrix was tried once and rejected, and the two
 * reasons it gives are the two things this component had to answer. The first was
 * that it plotted "six lonely pills in twenty-five cells"; the second was that it
 * ran two colour scales at once. So: every risk is drawn **twice** — hollow where
 * it sits with no control, filled where the Aegis control leaves it — with a
 * connector between, which roughly doubles the occupied cells and, more
 * importantly, makes the picture about the move. And the grid itself is
 * achromatic: the only hue in the frame is the residual band on the filled mark,
 * which is the same single meaning colour carries everywhere else on this page.
 *
 * **What this shows that the dumbbell cannot.** The dumbbell ranks risks by
 * `likelihood × impact`, and a product collapses its two factors. The claim this
 * deployment actually makes — stated in `platform/risk_map.py` — is that controls
 * move **likelihood**, not impact: a human gate does not make a wrongly-closed
 * request cheaper, it makes it near-impossible for the agent to reach alone. On
 * this grid that claim is visible as a shape: the connectors run horizontally,
 * and the one that also drops vertically is the one control that genuinely
 * shrinks the blast radius. No number on the page says that.
 *
 * The picture is never the only carrier (DESIGN.md §7): every mark carries an SVG
 * `<title>`, and the dumbbell list below repeats each risk's two coordinates as
 * digits.
 */

/** Neutral ink for the grid, the connector and the "before" mark. */
const TRACK_INK = '#98a2b3'

/**
 * The plot area inside the viewBox, in user units — **wider than it is tall**.
 *
 * A risk matrix is conventionally square, and square is what a first cut drew. But
 * both axes here are ordinal bands, not two readings of one metric, so nothing is
 * distorted by giving the cells a landscape aspect — and the card this sits in
 * shares a row with the headline tile, which is naturally short. A square grid made
 * that row 280px taller on one side than the other, which is dead canvas in the
 * exact shape the design rules call out. Flattening it costs the picture nothing
 * and buys back most of the difference.
 */
const PLOT_W = 300
const PLOT_H = 190
/** Gutters for the axis labels — left for impact, bottom for likelihood. */
const GUTTER_X = 34
const GUTTER_Y = 34
const PAD = 6

/**
 * The values an axis takes. The published `scale` is authoritative — the grid is
 * the backend's, not a hard-coded five — and the observed coordinates are the
 * fallback for a response that arrives with an empty scale, so the marks can
 * never land outside the frame that is drawn for them.
 */
function axisValues(published: number[], observed: number[]): number[] {
  const source = published.length > 0 ? published : observed
  return [...new Set(source)].sort((a, b) => a - b)
}

/** A mark to draw: the risk it belongs to and the cell it lands in. */
interface Mark {
  risk: RiskEntry
  ix: number
  iy: number
}

/** Group marks by the cell they land in, so co-located ones can be fanned out. */
function byCell(marks: Mark[]): Map<string, Mark[]> {
  const out = new Map<string, Mark[]>()
  for (const m of marks) {
    const cell = `${m.ix}:${m.iy}`
    const bucket = out.get(cell)
    if (bucket) bucket.push(m)
    else out.set(cell, [m])
  }
  return out
}

/**
 * Offset for the `j`-th of `k` marks sharing one cell. A single mark sits dead
 * centre; several fan onto a small ring so nine risks in seven cells stay nine
 * countable marks rather than one thick dot.
 */
function fan(j: number, k: number, radius: number): { dx: number; dy: number } {
  if (k <= 1) return { dx: 0, dy: 0 }
  const angle = (2 * Math.PI * j) / k - Math.PI / 2
  return { dx: Math.cos(angle) * radius, dy: Math.sin(angle) * radius }
}

interface RiskMatrixProps {
  risks: RiskEntry[]
  /** The likelihood × impact bands the response publishes. */
  scale: RiskScale
}

export function RiskMatrix({ risks, scale }: RiskMatrixProps): ReactElement {
  const xs = axisValues(
    scale.likelihood,
    risks.flatMap((r) => [r.likelihood, r.residual_likelihood]),
  )
  const ys = axisValues(scale.impact, risks.flatMap((r) => [r.impact, r.residual_impact]))

  const cellW = PLOT_W / Math.max(1, xs.length)
  const cellH = PLOT_H / Math.max(1, ys.length)
  const x0 = GUTTER_X
  const y0 = PAD
  const width = GUTTER_X + PLOT_W + PAD
  const height = PAD + PLOT_H + GUTTER_Y

  /** Centre of the cell at axis indices, before any fan-out. */
  const cx = (ix: number): number => x0 + (ix + 0.5) * cellW
  // Impact runs upward, so the last band is the top row.
  const cy = (iy: number): number => y0 + (ys.length - 1 - iy + 0.5) * cellH

  const inherent: Mark[] = risks.map((risk) => ({
    risk,
    ix: xs.indexOf(risk.likelihood),
    iy: ys.indexOf(risk.impact),
  }))
  const residual: Mark[] = risks.map((risk) => ({
    risk,
    ix: xs.indexOf(risk.residual_likelihood),
    iy: ys.indexOf(risk.residual_impact),
  }))

  const ring = Math.min(cellW, cellH) * 0.19
  const place = (marks: Mark[]): Map<string, { x: number; y: number }> => {
    const out = new Map<string, { x: number; y: number }>()
    for (const bucket of byCell(marks).values()) {
      bucket.forEach((m, j) => {
        const { dx, dy } = fan(j, bucket.length, ring)
        out.set(m.risk.id, { x: cx(m.ix) + dx, y: cy(m.iy) + dy })
      })
    }
    return out
  }
  const before = place(inherent)
  const after = place(residual)

  const movedDown = risks.filter((r) => r.residual_impact < r.impact).length

  return (
    <div className="flex flex-col gap-3">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full max-w-[520px] self-center"
        role="img"
        aria-label={`Likelihood by impact grid for ${risks.length} risks. Each risk is drawn twice: an open mark at its position with no control and a filled mark where the Aegis control leaves it. ${movedDown} of ${risks.length} controls reduce impact as well as likelihood; the rest move likelihood alone.`}
      >
        {/* The grid. Achromatic on purpose — the only hue in the frame is the
            residual band, so colour keeps the one meaning it has on this page. */}
        {ys.map((_, iy) =>
          xs.map((_, ix) => (
            <rect
              key={`${ix}:${iy}`}
              x={x0 + ix * cellW}
              y={y0 + (ys.length - 1 - iy) * cellH}
              width={cellW}
              height={cellH}
              fill="var(--surface-2)"
              stroke="var(--card)"
              strokeWidth={2}
            />
          )),
        )}

        {/* Axis ticks, taken from the published scale. */}
        {xs.map((v, ix) => (
          <text
            key={`x-${v}`}
            x={cx(ix)}
            y={y0 + PLOT_H + 15}
            textAnchor="middle"
            fill="var(--muted-foreground)"
            fontSize={10}
            fontFamily="var(--font-mono)"
          >
            {v}
          </text>
        ))}
        {ys.map((v, iy) => (
          <text
            key={`y-${v}`}
            x={x0 - 8}
            y={cy(iy) + 3.5}
            textAnchor="end"
            fill="var(--muted-foreground)"
            fontSize={10}
            fontFamily="var(--font-mono)"
          >
            {v}
          </text>
        ))}
        <text
          x={x0 + PLOT_W / 2}
          y={y0 + PLOT_H + 29}
          textAnchor="middle"
          fill="var(--muted-foreground)"
          fontSize={9.5}
          letterSpacing="0.08em"
        >
          LIKELIHOOD →
        </text>
        <text
          x={11}
          y={y0 + PLOT_H / 2}
          textAnchor="middle"
          fill="var(--muted-foreground)"
          fontSize={9.5}
          letterSpacing="0.08em"
          transform={`rotate(-90 11 ${y0 + PLOT_H / 2})`}
        >
          IMPACT →
        </text>

        {/* One group per risk: connector, "before", "after". Drawn in that order
            so no connector crosses over a mark it does not belong to. */}
        {risks.map((risk) => {
          const a = before.get(risk.id)
          const b = after.get(risk.id)
          if (!a || !b) return null
          return (
            <line
              key={`line-${risk.id}`}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={TRACK_INK}
              strokeOpacity={0.55}
              strokeWidth={1.75}
              strokeLinecap="round"
            />
          )
        })}
        {risks.map((risk) => {
          const a = before.get(risk.id)
          if (!a) return null
          return (
            <circle
              key={`before-${risk.id}`}
              cx={a.x}
              cy={a.y}
              r={4}
              fill="var(--card)"
              stroke={TRACK_INK}
              strokeWidth={1.5}
            >
              <title>{`${risk.id} ${risk.title} — before: likelihood ${risk.likelihood}, impact ${risk.impact}`}</title>
            </circle>
          )
        })}
        {risks.map((risk) => {
          const b = after.get(risk.id)
          if (!b) return null
          return (
            <circle
              key={`after-${risk.id}`}
              cx={b.x}
              cy={b.y}
              r={5}
              fill={SIGNALS[residualSignal(risk.residual)].hex}
              stroke="var(--card)"
              strokeWidth={1.5}
            >
              <title>{`${risk.id} ${risk.title} — after ${risk.control_name}: likelihood ${risk.residual_likelihood}, impact ${risk.residual_impact}, ${RESIDUAL_META[risk.residual].label.toLowerCase()} residual`}</title>
            </circle>
          )
        })}
      </svg>

      {/* The key: what the two mark shapes are, then what the one hue means. Every
          band ships a swatch *and* the word, so the verdict never rests on hue. */}
      <ul className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 text-[0.72rem] text-muted-foreground">
        <li className="flex items-center gap-1.5">
          <span
            className="size-2.5 shrink-0 rounded-full border-[1.5px] bg-card"
            style={{ borderColor: TRACK_INK }}
            aria-hidden
          />
          before
        </li>
        <li className="flex items-center gap-1.5">
          <span
            className="size-2.5 shrink-0 rounded-full"
            style={{ background: TRACK_INK }}
            aria-hidden
          />
          after, by band:
        </li>
        {RESIDUAL_ORDER.map((band) => (
          <li key={band} className="flex items-center gap-1.5">
            <span
              className="size-2.5 shrink-0 rounded-full"
              style={{ background: SIGNALS[residualSignal(band)].hex }}
              aria-hidden
            />
            {RESIDUAL_META[band].label}
          </li>
        ))}
      </ul>
    </div>
  )
}
