'use client'

import { Info } from 'lucide-react'
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
 * ## Why this is a cell and no longer a panel
 *
 * It was its own bordered section between the lanes and the answer, which put routing
 * on screen for the sixth time on a settled turn. It is now the fourth cell of the
 * decision strip, beside guardrails, sources and cost — the same kind of fact as those
 * three, read in the same glance.
 *
 * **A run with no `routing` event never gets a width drawn for it.** Not every run
 * routes, and filling that silence with the requested width would invent the one thing
 * this exists to source. The cell states the silence either way, because a cell cannot
 * simply vanish out of a four-column grid: when a width *was* asked for it is an
 * {@link Absence}, since "I asked for a team and the run says nothing about it" is
 * exactly the case a reader would otherwise fill in themselves; when nothing was asked
 * for there is no claim to correct, so it is the plain not-measured line the strip's
 * other cells use.
 */
export function WidthCell({
  state,
  requested,
}: {
  state: RunState
  /** The width the composer asked for, as this turn was sent. */
  requested: RunMode
}): ReactElement {
  if (state.routing === null) {
    if (requested.depth === 'auto') {
      return (
        <span
          className="text-[0.8125rem] leading-5 text-muted-foreground"
          title="This run emitted no routing event, and no width was asked for."
        >
          not measured
        </span>
      )
    }
    return (
      <Absence
        figure="Not reported"
        why="this run emitted no routing event, so nothing it sent back says how wide it ran"
        needed="a routing event from the supervisor"
        className="border-0 bg-transparent p-0"
      />
    )
  }

  const outcome = widthOutcome(requested, state.routing)

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="truncate text-sm font-medium text-foreground">{outcome.ran}</span>
        <Badge tone={outcome.differs ? 'risk' : 'neutral'} className="shrink-0">
          {outcome.differs ? 'not what was asked' : `chosen by ${outcome.decidedBy}`}
        </Badge>
      </div>

      {outcome.note !== null && (
        <p className="flex min-w-0 items-start gap-1.5 text-[0.72rem] leading-snug text-risk-ink">
          <Info aria-hidden className="mt-0.5 size-3 shrink-0" />
          <span className="min-w-0">
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
    </div>
  )
}
