import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

import { LandingScene } from './LandingScene'
import { LandingSection } from './LandingSection'

/**
 * Fan-out: how wide a turn runs, and who decided that.
 *
 * The chain above already shows *that* four lanes exist. This section is the part
 * the picture cannot carry — that the width is a control a person holds, and that
 * the run reports back who actually chose it. `decided_by: platform_cap` is the
 * interesting one: it is the platform telling a tenant that their own request was
 * narrowed by a ceiling they do not control, which is a thing most products would
 * simply not mention.
 *
 * The three widths and their sentences are lifted verbatim from `DEPTH_CHOICES`
 * in `components/console/runMode.ts` — the same strings the composer shows — so
 * the landing page cannot promise a mode the console spells differently.
 */

/** The three widths, exactly as the composer offers them. */
const WIDTHS = [
  { label: 'Auto', hint: 'The router picks the width and says who decided.' },
  { label: 'Single', hint: 'One lane. Fastest, cheapest, no fan-out.' },
  { label: 'Team', hint: 'Fan out to concurrent specialists, then synthesise.' },
] as const

/** The four answers the run's `routing` event can give for "who chose this width". */
const DECIDED_BY = [
  { value: 'auto', gloss: 'the classifier chose' },
  { value: 'user', gloss: 'you chose, and got it' },
  { value: 'tenant_default', gloss: 'no team roster on this tenant' },
  { value: 'platform_cap', gloss: 'your width was narrowed by the ceiling' },
] as const

export function TeamSection(): ReactElement {
  return (
    <LandingSection
      id="agents"
      tone="surface"
      eyebrow="Fan-out"
      title="You choose how wide it runs. The run tells you who actually decided."
      lead="The width is a control in the composer. The run names whoever settled it."
    >
      <div className="grid gap-x-14 gap-y-10 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:items-center">
        <LandingScene name="team" width={380} className="mx-auto lg:mx-0" />

        <div className="min-w-0">
          {/* A picture of the control itself. `aria-hidden` because the list
              directly under it names all three widths and says what each does —
              announcing the segments would read the same three words twice. */}
          <div
            aria-hidden
            className="mb-5 inline-flex max-w-full flex-wrap gap-1 rounded-lg border border-border bg-surface-2 p-1"
          >
            {WIDTHS.map((width, index) => (
              <span
                key={width.label}
                className={cn(
                  'rounded-md px-4 py-1.5 text-sm font-medium',
                  index === WIDTHS.length - 1
                    ? 'bg-blue-600 text-white'
                    : 'text-muted-foreground',
                )}
              >
                {width.label}
              </span>
            ))}
          </div>

          <ul className="divide-y divide-border border-y border-border">
            {WIDTHS.map((width) => (
              <li key={width.label} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-3.5">
                <span className="w-16 shrink-0 font-mono text-[0.8125rem] font-medium text-blue-700">
                  {width.label}
                </span>
                <span className="min-w-0 flex-1 text-pretty text-sm leading-relaxed text-foreground">
                  {width.hint}
                </span>
              </li>
            ))}
          </ul>

          <p className="eyebrow mt-8 mb-3">And the run answers with</p>
          <dl className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
            {DECIDED_BY.map((entry) => (
              <div key={entry.value} className="min-w-0">
                <dt className="tabular truncate font-mono text-[0.75rem] text-blue-700">
                  decided_by: {entry.value}
                </dt>
                <dd className="text-[0.8125rem] leading-relaxed text-muted-foreground">
                  {entry.gloss}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </LandingSection>
  )
}
