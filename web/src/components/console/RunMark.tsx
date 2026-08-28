'use client'

import { useReducedMotion } from 'motion/react'
import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import type { Beat } from './motion'
import {
  SEGMENTS,
  SPIN_SECONDS,
  brokenSegmentOf,
  markStateOf,
  segmentsOf,
  type MarkState,
} from './runMarkState'

/** The mark's geometry. A 48-unit box, an r=18 ring, an r=6 core. */
const CX = 24
const CY = 24
const R = 18
/** The gap between segments, in degrees. Small enough to read as one ring. */
const GAP = 6

/** A point on the ring, in SVG user units, at `angle` degrees clockwise from the top. */
function point(angle: number): string {
  const radians = ((angle - 90) * Math.PI) / 180
  return `${(CX + R * Math.cos(radians)).toFixed(3)} ${(CY + R * Math.sin(radians)).toFixed(3)}`
}

/** `count` equal arcs around the ring, each one short of its share by {@link GAP}. */
function arcs(count: number): string[] {
  const step = 360 / count
  return Array.from({ length: count }, (_unused, index) => {
    const from = index * step + GAP / 2
    const to = (index + 1) * step - GAP / 2
    const large = to - from > 180 ? 1 : 0
    return `M ${point(from)} A ${R} ${R} 0 ${large} 1 ${point(to)}`
  })
}

/** The six-arc ring, built once — the shape every state but `fanout` draws. */
const RING = arcs(SEGMENTS)

/**
 * The ink each state reads in. Only the one blue ramp and the three reserved status
 * hues, all of them {@link SIGNALS} tokens — the mark never introduces a colour.
 */
const TONE: Record<MarkState, string> = {
  idle: 'text-border',
  screening: 'text-blue-700',
  thinking: 'text-blue-700',
  fanout: 'text-blue-700',
  gated: 'text-risk-ink',
  blocked: 'text-block-ink',
  settled: 'text-ok-ink',
}

/**
 * Ring weight per state. This is the reduced-motion channel: `screening` differs from
 * `idle` by going from a hairline to a solid stroke, so a viewer who has turned motion
 * off still sees the state change rather than nothing at all.
 */
const WEIGHT: Record<MarkState, number> = {
  idle: 1.25,
  screening: 2.5,
  thinking: 2.5,
  fanout: 2.5,
  gated: 2.5,
  blocked: 2.5,
  settled: 2.5,
}


/**
 * The signature mark — a six-segment ring around a core, driven by the live run.
 *
 * ## Why six, and why a ring
 *
 * Six because `INPUT_CHAIN.length === OUTPUT_CHAIN.length === 6`: the ring is the rail
 * chain that runs around every answer, and the core is the model it runs around. The
 * geometry states the product's argument, which is the only reason a mark earns space on
 * an operator screen. It is abstract on purpose — no face, no creature; DESIGN.md §7 is
 * explicit that this system has no characters, and an enterprise governance jury is the
 * last audience for a mascot. It replaces `AssistantBot`, so the console's identity
 * elements went **down** by one.
 *
 * ## Every state is legible without motion
 *
 * Hue, stroke weight, arc count and core shape carry all seven states on their own;
 * motion only ever restates one. `idle` draws once and does not loop — DESIGN.md §6 bans
 * ambient loops on operator screens — and the rotation, the pulse and the spread exist
 * only while a run is live. `gated` is the state with *no* motion at all: the rotation
 * stops dead, and the stillness is the fact.
 *
 * The mark is `aria-hidden`. Every state it shows is stated in words elsewhere in the
 * panel; a decorative element that is a screen reader's only route to "this run was
 * blocked" would be a worse failure than the dead air it replaced.
 *
 * @param state - The run driving the mark, or `null` for a console with no run.
 * @param beat - The console's heartbeat. The core pulses once per wire event, keyed on
 *   `beat.seq`, and only while the run is thinking.
 */
export function RunMark({
  state,
  beat = null,
  className,
}: {
  state: RunState | null
  beat?: Beat | null
  className?: string
}): ReactElement {
  const reduced = useReducedMotion() ?? false
  const mark = markStateOf(state)
  const broken = brokenSegmentOf(state)
  const count = segmentsOf(state)
  const paths = count === SEGMENTS ? RING : arcs(count)

  // One pulse per wire event, and only while the run is thinking. `lastSignal` updates on
  // every event including `run_finished`, so a pulse that is not gated on the live state
  // beats for ever after the run has ended.
  const pulseKey = mark === 'thinking' && beat !== null ? beat.seq : 'still'
  const spin = SPIN_SECONDS[mark]

  return (
    <svg
      aria-hidden
      viewBox="0 0 48 48"
      className={cn('size-5 shrink-0', TONE[mark], className)}
      fill="none"
      stroke="currentColor"
      strokeWidth={WEIGHT[mark]}
      strokeLinecap="round"
    >
      {mark === 'settled' ? (
        /* Settled: the segments close. One unbroken ring is what "nothing is still
           open" looks like, and it is the only state that draws no gaps. */
        <circle cx={CX} cy={CY} r={R} />
      ) : (
        <g
          className={cn(spin !== undefined && !reduced && 'animate-mark-spin')}
          style={spin === undefined ? undefined : { animationDuration: `${spin}s` }}
        >
          {paths.map((d, index) =>
            /* The one missing segment of a blocked run sits at the rail the wire named.
               `brokenSegmentOf` returns null rather than guessing, and then the ring is
               whole and the hue alone carries the block. */
            index === broken ? null : (
              <path key={d} d={d} className={mark === 'idle' && !reduced ? 'animate-mark-draw' : undefined} />
            ),
          )}
        </g>
      )}

      {mark === 'gated' ? (
        /* The core squares off at the human gate — a shape change, not a colour change,
           so it survives both reduced motion and a monochrome projector. */
        <rect x={CX - 5} y={CY - 5} width={10} height={10} rx={1.5} fill="currentColor" stroke="none" />
      ) : (
        <circle
          key={pulseKey}
          cx={CX}
          cy={CY}
          r={6}
          fill={mark === 'idle' || mark === 'screening' ? 'none' : 'currentColor'}
          className={cn(mark === 'thinking' && !reduced && 'animate-mark-core')}
        />
      )}
    </svg>
  )
}
