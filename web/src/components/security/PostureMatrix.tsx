'use client'

import { CircleCheck, CircleSlash, TriangleAlert, type LucideIcon } from 'lucide-react'
import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/primitives/tooltip'
import { cn } from '@/lib/utils'
import type { PostureEntry, PostureStatus } from '@/lib/api/platform'

/** The three bands, worst last, with the icon and the word each one always ships with. */
const BAND: Record<
  PostureStatus,
  { label: string; icon: LucideIcon; cell: string; ink: string }
> = {
  enforced: {
    label: 'enforced',
    icon: CircleCheck,
    cell: 'border-ok bg-ok/15 text-ok-ink',
    ink: 'text-ok-ink',
  },
  partial: {
    label: 'partial',
    icon: TriangleAlert,
    cell: 'border-risk bg-risk/15 text-risk-ink',
    ink: 'text-risk-ink',
  },
  not_covered: {
    label: 'not covered',
    icon: CircleSlash,
    cell: 'border-block bg-block/15 text-block-ink',
    ink: 'text-block-ink',
  },
}

const ORDER: PostureStatus[] = ['enforced', 'partial', 'not_covered']

/** Coerce a possibly-widened status string to a known band, defaulting honest. */
function bandOf(status: PostureStatus | string): PostureStatus {
  return status === 'enforced' || status === 'partial' || status === 'not_covered'
    ? status
    : 'not_covered'
}

/**
 * The whole OWASP-Agentic surface as one board — one cell per threat, carrying
 * that threat's id, its band icon and its band tint.
 *
 * A seventeen-row table answers "what is the posture of T7?" perfectly and
 * answers "how much of the surface is actually held down?" not at all — you have
 * to read every row and keep a tally in your head. The board answers the second
 * question in one glance and stays honest about the first: every cell is a real
 * entry, the id is on the face of it, and the control that holds it is one hover
 * away. The table below is still the record; this is the shape of it.
 *
 * Colour is never alone. Each cell carries its band's icon, the legend spells the
 * three words out with counts, and the tooltip names the control.
 */
export function PostureMatrix({ entries }: { entries: PostureEntry[] }): ReactElement {
  const counts: Record<PostureStatus, number> = { enforced: 0, partial: 0, not_covered: 0 }
  for (const e of entries) counts[bandOf(e.status)] += 1

  // Worst first: a reader should meet the gaps before the wins.
  const ordered = [...entries].sort(
    (a, b) => ORDER.indexOf(bandOf(b.status)) - ORDER.indexOf(bandOf(a.status)),
  )

  return (
    <div className="flex flex-col gap-4">
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(4.5rem,1fr))] gap-2">
        {ordered.map((entry) => {
          const band = BAND[bandOf(entry.status)]
          const Icon = band.icon
          return (
            <li key={entry.threat_id}>
              <Tooltip>
                <TooltipTrigger
                  type="button"
                  className={cn(
                    'flex w-full flex-col items-start gap-1.5 rounded-md border px-2 py-2 text-left transition-colors duration-[--dur-fast] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    band.cell,
                  )}
                >
                  <Icon className="size-3.5 shrink-0" aria-hidden />
                  <Figure className="w-full truncate font-semibold uppercase">
                    {entry.threat_id}
                  </Figure>
                  <span className="sr-only">
                    {entry.name} — {band.label}
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs text-xs leading-relaxed">
                  <span className="font-semibold">{entry.name}</span> — {band.label}.{' '}
                  {entry.control} ({entry.module} · {entry.mechanism}).
                </TooltipContent>
              </Tooltip>
            </li>
          )
        })}
      </ul>

      <ul className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-border pt-3">
        {ORDER.map((band) => {
          const meta = BAND[band]
          const Icon = meta.icon
          return (
            <li key={band} className="flex items-center gap-2">
              <Icon className={cn('size-3.5 shrink-0', meta.ink)} aria-hidden />
              <Figure size="stat" className="text-foreground">
                {counts[band]}
              </Figure>
              <span className="text-[0.8125rem] text-muted-foreground">{meta.label}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
