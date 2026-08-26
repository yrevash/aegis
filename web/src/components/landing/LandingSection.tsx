import type { ReactElement, ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface LandingSectionProps {
  /** Anchor target — the header nav links to these. */
  id?: string
  /** The small mono tag above the title. Two or three words. */
  eyebrow: string
  /** The section's claim, in sentence case. */
  title: string
  /** One or two sentences under the claim, at a readable measure. */
  lead?: ReactNode
  /**
   * Which ground the section sits on. Alternating them is what tells the
   * sections apart now that they no longer share a ruled header band.
   */
  tone?: 'canvas' | 'surface'
  children: ReactNode
  className?: string
}

/**
 * One section of the public page: a stacked head, then the artefact.
 *
 * **The ruled two-column header band is gone.** Every section used to open with
 * an eyebrow and a heading on the left, a qualifying note on the right, and a
 * hairline under both — five times in a row. It reads as an editorial page for
 * about one section and as a template for the other four, and it is the exact
 * silhouette DESIGN.md §9 calls "repetitive equal-thirds sections": by the third
 * one a reader has stopped reading headings, and the sections carrying *live
 * data* look identical to the ones carrying static copy.
 *
 * So the head is a single stacked column at a readable measure, and the sections
 * are told apart by what they sit on and what they hold — the navy chain, a white
 * band with a scene in it, a dense grid on the canvas — rather than by a
 * repeated rule. `scroll-mt-16` clears the sticky header so an anchor never lands
 * with its own heading hidden behind the bar.
 */
/**
 * The section heading, one step under `.t-hero`.
 *
 * `--font-display`, because the display voice is what carries this page. That token
 * now resolves to the same family as `--font-sans` — IBM Plex Sans is a superfamily,
 * so the voice comes from weight and tracking rather than from a second face.
 * `globals.css` stops at `.t-title` (18px), which is
 * a card title — a landing section heading has no utility to inherit, and adding
 * one to the global sheet for a single page would put a marketing type step in
 * front of every product screen.
 */
const SECTION_TITLE =
  'font-display text-[1.75rem] leading-9 font-semibold tracking-[-0.02em] sm:text-[2.125rem] sm:leading-[1.15]'

export function LandingSection({
  id,
  eyebrow,
  title,
  lead,
  tone = 'canvas',
  children,
  className,
}: LandingSectionProps): ReactElement {
  return (
    <section
      id={id}
      className={cn('scroll-mt-16', tone === 'surface' ? 'bg-surface' : 'bg-background', className)}
    >
      <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6 sm:py-20">
        <header className="mb-10 max-w-[58ch]">
          <p className="eyebrow mb-3">{eyebrow}</p>
          <h2 className={cn(SECTION_TITLE, 'text-balance text-foreground')}>{title}</h2>
          {lead == null ? null : (
            <p className="mt-4 text-pretty text-[1.0625rem] leading-relaxed text-muted-foreground">
              {lead}
            </p>
          )}
        </header>
        {children}
      </div>
    </section>
  )
}
