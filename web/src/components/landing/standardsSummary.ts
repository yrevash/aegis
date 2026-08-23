/**
 * The standards band's arithmetic, kept out of the component so it can be tested.
 *
 * Everything here is a function of the payload `GET /v1/platform/standards` sent. **No
 * number is written down in this file, and none may be**: the whole reason the
 * endpoint exists is that the landing page is public while `GET /v1/compliance` is
 * platform-staff-only, and a count typed into a marketing section is a count nobody
 * re-derives when a control changes state. `web/tests/landing/standardsSummary.test.mjs`
 * asserts that this module and the band component contain no control totals.
 *
 * Pure and framework-free — no React, no icons, no DOM — so the segment maths runs
 * under `node --test` directly.
 */

import type { FrameworkSummary, StandardsCoverage } from '@/lib/api/standards'

/** The four states a control can be in, exactly as the API spells them. */
export type ControlState = 'enforced' | 'partial' | 'not_implemented' | 'not_applicable'

/**
 * Best first: a reader meets what holds before what does not.
 *
 * The order is deliberate and it is not flattery — the two honest states come last
 * *and they are drawn*, at full width, in their own colours. A band that ordered by
 * count, or that folded "not implemented" into an "other", would be the padding this
 * surface exists to refuse.
 */
export const STATE_ORDER: readonly ControlState[] = [
  'enforced',
  'partial',
  'not_implemented',
  'not_applicable',
]

/**
 * How each state is worded and coloured.
 *
 * The three reserved status hues plus neutral, and no new hue (DESIGN.md §2).
 * `not_applicable` takes the neutral one on purpose: a control governing something
 * Aegis does not do is not a failure, and painting it red would inflate the gap count
 * with rows that are not gaps.
 *
 * These are the same tones `web/src/components/compliance/CoverageStrip.tsx` uses on
 * the console screen, restated rather than imported: that module is a `'use client'`
 * component pulling in lucide icons and the generated console types, and the landing
 * page is not the place to drag either across. The two are checked against each other
 * by eye once; if they ever diverge the console is the authority.
 */
export const STATE_META: Record<ControlState, { label: string; fill: string; ink: string }> = {
  enforced: { label: 'enforced', fill: 'bg-ok', ink: 'text-ok-ink' },
  partial: { label: 'partial', fill: 'bg-risk', ink: 'text-risk-ink' },
  not_implemented: { label: 'not implemented', fill: 'bg-block', ink: 'text-block-ink' },
  not_applicable: {
    label: 'not applicable',
    fill: 'bg-muted-foreground/40',
    ink: 'text-muted-foreground',
  },
}

/** Read one state's count off a coverage record. */
export function countOf(coverage: StandardsCoverage, state: ControlState): number {
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

/** One drawn band of the proportional strip. */
export interface Segment {
  state: ControlState
  label: string
  fill: string
  ink: string
  count: number
  /** Share of the total, as a CSS percentage string. */
  width: string
}

/**
 * The strip's segments, widest-meaningful-first in {@link STATE_ORDER}.
 *
 * **A zero is not drawn** (DESIGN.md §4): a state with no controls in it emits no
 * segment and no legend key, because a hairline slice for a state that never occurred
 * teaches a reader to stop reading the legend. A total of zero — which would mean the
 * control table had emptied — yields no segments at all, and the band renders its
 * absence rather than a full-width nothing.
 */
export function segments(coverage: StandardsCoverage): Segment[] {
  const total = coverage.total
  if (total <= 0) return []
  return STATE_ORDER.filter((state) => countOf(coverage, state) > 0).map((state) => {
    const count = countOf(coverage, state)
    return {
      state,
      label: STATE_META[state].label,
      fill: STATE_META[state].fill,
      ink: STATE_META[state].ink,
      count,
      width: `${(count / total) * 100}%`,
    }
  })
}

/**
 * The frameworks in the order the band draws them: India first, then international.
 *
 * The API already serves them in that order and this preserves it rather than
 * re-sorting — the ordering is a jurisdictional statement made by the compliance
 * authority, not a display preference. What this *does* add is the grouping boundary
 * the grid draws a label at, computed rather than assumed, so a framework added to
 * either jurisdiction lands under the right heading with no edit here.
 */
export interface JurisdictionGroup {
  jurisdiction: string
  frameworks: FrameworkSummary[]
}

/** Group the served frameworks by jurisdiction, preserving the served order. */
export function byJurisdiction(frameworks: readonly FrameworkSummary[]): JurisdictionGroup[] {
  const groups: JurisdictionGroup[] = []
  for (const framework of frameworks) {
    const last = groups.at(-1)
    if (last !== undefined && last.jurisdiction === framework.jurisdiction) {
      last.frameworks.push(framework)
    } else {
      groups.push({ jurisdiction: framework.jurisdiction, frameworks: [framework] })
    }
  }
  return groups
}
