import type { ReactElement, ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface LandingSectionProps {
  /** Anchor target — the header nav links to these. */
  id?: string
  /** The small mono tag above the title. Two or three words. */
  eyebrow: string
  /** The section's claim, in sentence case. */
  title: string
  /**
   * One line that qualifies the claim, set beside it rather than beneath. Most
   * sections have one; it is what fills the right half of the header band and
   * stops five sections in a row from being the same centred stack.
   */
  note?: ReactNode
  /** A tighter measure for a section that is mostly one artefact. */
  width?: 'wide' | 'narrow'
  children: ReactNode
  className?: string
}

/**
 * One section of the public page: a header band, then the artefact.
 *
 * Every section on this page used to open with a centred eyebrow over a centred
 * `text-3xl` heading, five times, which is the "repetitive equal sections"
 * failure DESIGN.md §8 names outright — by the third one a reader has stopped
 * reading headings, and the two sections that carry *live data* look exactly
 * like the two that carry static copy.
 *
 * So the header is one asymmetric band instead: the claim on the left at the
 * width of a headline, its qualifier on the right at the width of a caption,
 * split by a hairline. It reads as an editorial page rather than a deck, and it
 * gives the sections a shared rhythm without giving them the same silhouette.
 */
export function LandingSection({
  id,
  eyebrow,
  title,
  note,
  width = 'wide',
  children,
  className,
}: LandingSectionProps): ReactElement {
  // `scroll-mt-16` clears the 64px sticky header: without it every anchor in the
  // nav landed with its own heading hidden behind the bar.
  return (
    <section id={id} className={cn('scroll-mt-16 border-b border-border', className)}>
      <div
        className={cn(
          'mx-auto px-6 py-16 sm:py-20',
          width === 'narrow' ? 'max-w-4xl' : 'max-w-6xl',
        )}
      >
        <div className="mb-10 grid gap-x-12 gap-y-4 border-b border-border pb-8 lg:grid-cols-[minmax(0,7fr)_minmax(0,4fr)] lg:items-end">
          <div className="min-w-0">
            <p className="eyebrow mb-3">{eyebrow}</p>
            <h2 className="text-balance text-[1.75rem] leading-8 font-semibold tracking-[-0.02em] text-foreground sm:text-[2rem] sm:leading-10">
              {title}
            </h2>
          </div>
          {note == null ? null : (
            <p className="max-w-prose text-pretty text-sm leading-relaxed text-muted-foreground lg:pb-1">
              {note}
            </p>
          )}
        </div>
        {children}
      </div>
    </section>
  )
}
