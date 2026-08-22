'use client'

import { CircleSlash } from 'lucide-react'
import type { ReactElement } from 'react'

import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'

/**
 * A stated absence as **one line** — the compact form DESIGN.md §5 asks for.
 *
 * `primitives/Receipt.tsx` already owns the vocabulary: {@link Absence} is a
 * glyph, the figure's name, why it is not recorded, and what would have to be
 * emitted before it could be. Nothing here disagrees with any of that — the
 * glyph, the wash and the `to measure it` wording are lifted verbatim, so a
 * reader who has learned one has learned the other.
 *
 * What changes is **where the sentences live**. §5 says a figure that cannot be
 * sourced "is also never three sentences — it is a compact stated absence", and
 * §4 spells the same rule for the panel body: *one line, not three*. The block
 * form is right when the absence **is** the panel (the pipeline's "no figures at
 * all"); it is wrong in a 230px triage tile, where three wrapped sentences made
 * the honest gaps the longest text on the ops overview. On this page the
 * absence is a slot, not a subject.
 *
 * So the face carries the two things a reader needs at a glance — *which figure*
 * and *that it is not recorded* — and the reasoning moves one layer down into an
 * {@link InfoTip}, reachable by hover **and** keyboard focus, behind a trigger
 * whose `aria-label` names its subject. **Nothing is dropped.** The claim, the
 * reason and the remediation are all still on the page; they stopped being
 * always-on. Deleting them would be the regression this component exists to
 * avoid (§4).
 *
 * The state is never a hue: `CircleSlash` and the words "not recorded" are what
 * say it, and the dashed hairline is the third cue rather than the only one.
 *
 * @example
 * <AbsenceMark
 *   figure="Run percentiles"
 *   why="No run has been recorded in this process's window yet."
 *   needed="one completed run — the window fills from GET /latency"
 * />
 */
export function AbsenceMark({
  figure,
  why,
  needed,
  className,
}: {
  /** The figure that would have gone here, named exactly as a reader expects it. */
  figure: string
  /** Why it is not recorded. The honest reason, not an apology. */
  why: string
  /** What would have to be emitted or stored before the figure could exist. */
  needed?: string | null
  className?: string
}): ReactElement {
  return (
    <div
      data-slot="absence-mark"
      className={cn(
        'flex min-w-0 items-start gap-2 rounded-md border border-dashed border-border bg-surface-2/40 px-3 py-2',
        className,
      )}
    >
      <CircleSlash className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <p className="min-w-0 flex-1 text-[0.8125rem] leading-5 text-pretty break-words text-foreground">
        {figure}{' '}
        {/* One unit: broken across a line it reads as the word "not" ending a
            figure's name, which is the opposite of what it says. */}
        <span className="eyebrow whitespace-nowrap">not recorded</span>
      </p>
      <InfoTip label={`Why ${figure} is not recorded`} className="mt-0.5 shrink-0">
        <span className="block space-y-1.5">
          <span className="block">{why}</span>
          {needed == null || needed === '' ? null : (
            <span className="block text-foreground">
              {/* The margin is the visual gap; the space is the one a screen reader
                  reads, without which the label runs into the sentence. */}
              <span className="eyebrow mr-1.5">to measure it</span>{' '}
              {needed}
            </span>
          )}
        </span>
      </InfoTip>
    </div>
  )
}
