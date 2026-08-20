import { Check } from 'lucide-react'
import type { ReactElement } from 'react'

import { LandingSection } from './LandingSection'

/**
 * Production posture as two lists: what runs today, and what does not yet.
 *
 * Split shipped / next so the page never implies a planned capability already
 * exists — that split is the whole point of the section and it stays.
 *
 * What changed is the shape. Both lists were `rounded-full` chips, and the
 * shipped ones were tinted `--ok` green: pill overuse (DESIGN.md §8) spending a
 * reserved status colour on a marketing label. Two columns of ruled rows say
 * the same thing without either. The tick stays on the shipped column — it is
 * an icon beside a word, which is exactly how a status is allowed to read — and
 * the "next" column carries no mark at all, because the absence of a tick is
 * the information.
 */

const RUNNING = [
  'Multi-tenant RLS + budgets',
  'Durable resumable runs',
  'Native Neo4j + embedded vectors',
  'OTel tracing + audit log',
  'Offline + CI eval gates',
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
      id="roadmap"
      eyebrow="Roadmap"
      title="Built for production, with the next steps named."
      note="Nothing in the right-hand column is on any screen in the console. It is here so the left-hand column can be read as a complete list rather than a selection."
      width="narrow"
    >
      <div className="grid gap-x-12 gap-y-10 sm:grid-cols-2">
        <div>
          <h3 className="eyebrow mb-3 text-foreground">Running today</h3>
          <ul className="divide-y divide-border border-t border-border">
            {RUNNING.map((item) => (
              <li key={item} className="flex items-start gap-2.5 py-3 text-sm text-foreground">
                <Check
                  aria-hidden
                  className="mt-0.5 size-4 shrink-0 text-ok-ink"
                  strokeWidth={2.5}
                />
                <span className="text-pretty">{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h3 className="eyebrow mb-3">Not built yet</h3>
          <ul className="divide-y divide-border border-t border-border">
            {NEXT.map((item) => (
              <li key={item} className="py-3 pl-[1.625rem] text-sm text-pretty text-muted-foreground">
                {item}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </LandingSection>
  )
}
