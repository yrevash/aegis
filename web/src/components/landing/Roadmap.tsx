import { Check } from 'lucide-react'
import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

import { LandingScene } from './LandingScene'
import { LandingSection } from './LandingSection'

/**
 * Production posture as two lists: what runs today, and what does not yet.
 *
 * The split is the whole section. A roadmap that runs the shipped and the planned
 * together lets a reader assume the planned already exists, and this is the last
 * thing on a page whose entire argument is that it does not overstate itself.
 *
 * The tick sits only on the left column, because the absence of a tick is the
 * information. Neither list is a chip: `rounded-full` on both, with the shipped
 * ones tinted `--ok` green, was pill overuse spending a reserved status colour on
 * a marketing label (DESIGN.md §9).
 *
 * `Native Neo4j + embedded vectors` was stale — the vectors moved back into
 * Qdrant, and a "running today" list that names the wrong store is the same class
 * of error as a hardcoded module count.
 */

/**
 * The two column heads.
 *
 * Not `.eyebrow` with a colour override: `.eyebrow` sets its own `color` and both
 * classes land in the same cascade layer, so which one wins is decided by
 * emission order. The shipped column had been reading in the muted grey of the
 * unshipped one, which is the single distinction this section exists to make.
 */
const COLUMN_HEAD = 'font-mono text-[0.68rem] font-medium tracking-[0.16em] uppercase'

const RUNNING = [
  'Multi-tenant RLS, budgets and an append-only audit log',
  'Durable, resumable runs across an approval',
  'Neo4j graph and Qdrant vectors, natively installed',
  'OpenTelemetry tracing on every run',
  'Offline and CI evaluation gates on retrieval quality',
]

const NEXT = [
  'Horizontal workers',
  'Managed store tiers',
  'Installable domain packs',
  'Live eval sampling',
]

export function Roadmap(): ReactElement {
  return (
    <LandingSection
      id="status"
      eyebrow="Posture"
      title="What runs today, and what does not."
      lead="Nothing in the right-hand column is on any screen in the console. It is here so the left-hand column can be read as a complete list rather than a flattering selection."
    >
      <div className="grid gap-x-14 gap-y-10 lg:grid-cols-[minmax(0,4fr)_minmax(0,8fr)] lg:items-start">
        <LandingScene name="running" width={280} className="mx-auto lg:mx-0" />

        <div className="grid gap-x-14 gap-y-10 sm:grid-cols-2">
          <div>
            <h3 className={cn(COLUMN_HEAD, 'mb-3 text-foreground')}>Running today</h3>
            <ul className="divide-y divide-border border-t border-border">
              {RUNNING.map((item) => (
                <li key={item} className="flex items-start gap-2.5 py-3 text-sm text-foreground">
                  <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-ok-ink" strokeWidth={2.5} />
                  <span className="text-pretty">{item}</span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h3 className={cn(COLUMN_HEAD, 'mb-3 text-muted-foreground')}>Not built yet</h3>
            <ul className="divide-y divide-border border-t border-border">
              {NEXT.map((item) => (
                <li
                  key={item}
                  className="py-3 pl-[1.625rem] text-sm text-pretty text-muted-foreground"
                >
                  {item}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </LandingSection>
  )
}
