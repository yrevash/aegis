'use client'

import {
  CircleCheck,
  CircleDashed,
  CircleSlash,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { cn } from '@/lib/utils'
import type { ComplianceCoverage, ControlState } from '@/lib/api/platform'

/**
 * The four states, worst-last, each with the icon, word and hue it always ships with.
 *
 * Four states out of the three reserved status hues plus neutral — no new colour is
 * introduced (DESIGN.md §2). `not_applicable` is deliberately the neutral one: a
 * control that governs something Aegis does not do is not a failure, and painting it
 * red would inflate the gap count with rows that are not gaps.
 */
export const STATE_META: Record<
  ControlState,
  { label: string; icon: LucideIcon; ink: string; fill: string; cell: string }
> = {
  enforced: {
    label: 'enforced',
    icon: CircleCheck,
    ink: 'text-ok-ink',
    fill: 'bg-ok',
    cell: 'border-ok bg-ok/15 text-ok-ink',
  },
  partial: {
    label: 'partial',
    icon: TriangleAlert,
    ink: 'text-risk-ink',
    fill: 'bg-risk',
    cell: 'border-risk bg-risk/15 text-risk-ink',
  },
  not_implemented: {
    label: 'not implemented',
    icon: CircleSlash,
    ink: 'text-block-ink',
    fill: 'bg-block',
    cell: 'border-block bg-block/15 text-block-ink',
  },
  not_applicable: {
    label: 'not applicable',
    icon: CircleDashed,
    ink: 'text-muted-foreground',
    fill: 'bg-muted-foreground/40',
    cell: 'border-border bg-surface-2 text-muted-foreground',
  },
}

/** Best first: a reader meets what holds before what does not. */
export const STATE_ORDER: ControlState[] = [
  'enforced',
  'partial',
  'not_implemented',
  'not_applicable',
]

/** Coerce a possibly-widened state string to a known band, defaulting honest. */
export function stateOf(state: ControlState | string): ControlState {
  return state === 'enforced' ||
    state === 'partial' ||
    state === 'not_implemented' ||
    state === 'not_applicable'
    ? state
    : 'not_implemented'
}

/** Read one state's count off a coverage record. */
function countOf(coverage: ComplianceCoverage, state: ControlState): number {
  switch (state) {
    case 'enforced':
      return coverage.enforced
    case 'partial':
      return coverage.partial
    case 'not_implemented':
      return coverage.not_implemented
    case 'not_applicable':
      return coverage.not_applicable
  }
}

/**
 * A proportional coverage strip — how much of a framework is actually held down,
 * answered before anything is described.
 *
 * This is the mark DESIGN.md §4 asks a status surface to open with. Ninety-one
 * control rows answer "what is the state of ISO A.8.3?" perfectly and answer "how
 * much of this framework do we actually hold?" not at all, because the reader has to
 * keep a tally in their head down the page.
 *
 * **Zero is not drawn.** A state with no controls emits no segment, because a
 * zero-width slice is the chart spelling of zero-fill and teaches a reader to stop
 * trusting the legend.
 *
 * Colour is never the carrier: the strip has an `aria-label` spelling out every
 * count, and the legend beside it (when shown) carries an icon and a word per state.
 */
export function CoverageStrip({
  coverage,
  size = 'md',
  className,
  enforcedOnly = false,
}: {
  coverage: ComplianceCoverage
  /** `sm` for the framework list, `md` for the headline board. */
  size?: 'sm' | 'md'
  className?: string
  /**
   * Draw the enforced share only; everything else reads as unfilled track.
   *
   * A three-colour bar that is 40% green, 40% amber and 15% red is read as "60%
   * broken" in the second a reviewer gives it — amber and red are alarm colours, and
   * `partial` is not an alarm. One filled proportion against a neutral track says the
   * true thing (this much is enforced) without the palette editorialising about the
   * rest.
   *
   * The `aria-label` still names every state, because a screen-reader user gets one
   * description and it should be the complete one.
   */
  enforcedOnly?: boolean
}): ReactElement {
  const total = coverage.total || 1
  const present = STATE_ORDER.filter(
    (state) => countOf(coverage, state) > 0 && (!enforcedOnly || state === 'enforced'),
  )
  const description = present
    .map((state) => `${countOf(coverage, state)} ${STATE_META[state].label}`)
    .join(', ')

  return (
    <div
      role="img"
      aria-label={
        present.length === 0
          ? 'No controls mapped'
          : `${coverage.total} controls: ${description}`
      }
      className={cn(
        'flex w-full min-w-0 overflow-hidden rounded-full bg-surface-2',
        size === 'sm' ? 'h-1.5' : 'h-2.5',
        className,
      )}
    >
      {present.map((state) => (
        <span
          key={state}
          className={cn('block h-full', STATE_META[state].fill)}
          style={{ width: `${(countOf(coverage, state) / total) * 100}%` }}
        />
      ))}
    </div>
  )
}

/**
 * The strip's legend — one row of icon + count + word per state that occurred.
 *
 * Separate from the strip because the framework list shows twelve strips and needs
 * exactly one legend; repeating it under each would be the "two marks of one field"
 * duplication DESIGN.md §4 names.
 */
export function CoverageLegend({
  coverage,
  className,
  enforcedOnly = false,
}: {
  coverage: ComplianceCoverage
  className?: string
  /**
   * Show only the enforced count, and leave the rest to the disclosure below.
   *
   * The platform is demonstrated in ten to fifteen minutes. A legend that opens with
   * `62 partial · 19 not implemented` makes the first thing a reviewer reads a list of
   * caveats, when the fact the page exists to carry is that **38 controls are
   * enforced** with a file, route or test behind each one.
   *
   * Nothing is deleted — the other states stay one click away, because `partial` means
   * *implemented with a named gap*, and in an enterprise room that gap sentence is
   * often the most credible thing on the screen. See DESIGN.md §4.
   */
  enforcedOnly?: boolean
}): ReactElement {
  const shown = enforcedOnly
    ? STATE_ORDER.filter((state) => state === 'enforced')
    : STATE_ORDER
  return (
    <ul className={cn('flex flex-wrap items-center gap-x-6 gap-y-2', className)}>
      {shown.filter((state) => countOf(coverage, state) > 0).map((state) => {
        const meta = STATE_META[state]
        const Icon = meta.icon
        return (
          <li key={state} className="flex min-w-0 items-center gap-2">
            <Icon className={cn('size-3.5 shrink-0', meta.ink)} aria-hidden />
            <Figure size="stat" className="text-foreground">
              {countOf(coverage, state)}
            </Figure>
            <span className="text-[0.8125rem] text-muted-foreground">{meta.label}</span>
          </li>
        )
      })}
    </ul>
  )
}
