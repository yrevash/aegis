'use client'

import { GitBranch, Info } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import type { RunState } from '@/state/runReducer'

import { widthOutcome, type RunMode } from './runMode'

/**
 * What the run actually ran at, read off its own `routing` event.
 *
 * The composer asks for a width; the run decides one. Those are two different facts and
 * the console had only ever shown the first — the chip stayed lit on `Team of 5` whether
 * the run fanned out to five, was clamped to three by the tenant's `max_parallel_agents`,
 * or never fanned out at all because the tenant has no team roster. A control that
 * reports its own request back as an outcome is the "figure with no origin" DESIGN.md §5
 * exists to forbid, applied to a setting instead of a number.
 *
 * So every word here comes from the run: `depth`, `fanout`, `decided_by` and the router's
 * own `reason`. Where the request and the outcome differ, the difference is stated —
 * once, in one line, from {@link widthOutcome}.
 *
 * **A run with no `routing` event never gets a width drawn for it.** Not every run
 * routes, and filling that silence with the requested width would invent the one thing
 * this panel exists to source. When nothing was asked for either, there is no claim to
 * correct and the panel simply does not appear; when a width *was* asked for, the
 * silence is stated as an {@link Absence}, because "I asked for a team and the run says
 * nothing about it" is exactly the case a reader would otherwise fill in themselves.
 */
export function WidthReceipt({
  state,
  requested,
}: {
  state: RunState
  /** The width the composer asked for, as this turn was sent. */
  requested: RunMode
}): ReactElement | null {
  if (state.routing === null) {
    if (requested.depth === 'auto') return null
    return (
      <Absence
        figure="Width"
        why="this run emitted no routing event, so nothing it sent back says how wide it ran"
        needed="a routing event from the supervisor"
        className="px-3 py-2"
      />
    )
  }

  const outcome = widthOutcome(requested, state.routing)

  return (
    <section
      aria-label="Width"
      className="flex flex-col gap-1.5 rounded-lg border border-border bg-surface-2/30 px-3 py-2"
    >
      <div className="flex flex-wrap items-center gap-2">
        <GitBranch aria-hidden className="size-3.5 shrink-0 text-blue-700" />
        <span className="eyebrow">Width</span>
        <span className="text-[0.82rem] font-medium text-foreground">{outcome.ran}</span>
        <Badge tone={outcome.differs ? 'risk' : 'neutral'}>
          {outcome.differs ? 'not what was asked' : `chosen by ${outcome.decidedBy}`}
        </Badge>
      </div>

      {outcome.note !== null && (
        <p className="flex items-start gap-1.5 text-[0.76rem] leading-snug text-risk-ink">
          <Info aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          <span>
            {outcome.note} Decided by {outcome.decidedBy}.
          </span>
        </p>
      )}

      <Receipt
        variant="inline"
        label="Decided by"
        origin={outcome.decidedByCode}
        detail={outcome.reason === '' ? null : outcome.reason}
      />
    </section>
  )
}
