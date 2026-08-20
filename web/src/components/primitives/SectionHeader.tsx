import type { ReactElement, ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** Which heading element the title becomes. See {@link SectionHeader}. */
export type HeadingLevel = 'h1' | 'h2' | 'h3'

/** The type ramp from DESIGN.md §3 — display for a page, title for a section. */
const HEADING: Record<HeadingLevel, string> = {
  h1: 'text-[1.75rem] leading-8 font-semibold tracking-[-0.02em]',
  h2: 'text-xl leading-7 font-semibold tracking-[-0.01em]',
  h3: 'text-base leading-6 font-semibold',
}

interface SectionHeaderProps {
  /** The small mono tag above the title. Two or three words at most. */
  eyebrow?: string
  /** The heading itself. Sentence case — not Title Case On Every Header. */
  title: string
  /**
   * The heading element, so the page has a real document outline. `h1` is the
   * page title and there is one per screen; sections are `h2`, panels inside a
   * section are `h3`. This is the only reason the prop exists — size follows
   * from the level rather than being chosen separately.
   */
  as?: HeadingLevel
  /**
   * One line of context, and only when the title alone cannot carry it. Most
   * headers do not need this; per-panel explainer prose is what made the old
   * card `description` prop dead weight.
   */
  note?: ReactNode
  /** Right-aligned slot — a count, a filter, one control. */
  right?: ReactNode
  className?: string
}

/**
 * The heading of a page or a section, with its outline level made explicit.
 *
 * Two problems, one component. The visible one is that every screen re-invented
 * the eyebrow / title / right-hand-control row, and they drifted apart. The
 * invisible one is worse: the console has 65 pages and fourteen `<h2>` elements
 * between them, because titles were being set as styled `<p>` and `<div>`. A
 * screen reader's heading list — the fastest way anyone navigates a dense
 * console — was therefore empty on most pages.
 *
 * @example
 * <SectionHeader as="h1" eyebrow="governance" title="Spend caps" />
 * <SectionHeader title="Recent audit rows" right={<Figure>{rows.length}</Figure>} />
 */
export function SectionHeader({
  eyebrow,
  title,
  as = 'h2',
  note,
  right,
  className,
}: SectionHeaderProps): ReactElement {
  const Heading = as
  return (
    <div
      data-slot="section-header"
      className={cn('flex items-start justify-between gap-4', className)}
    >
      <div className="min-w-0">
        {eyebrow == null ? null : <p className="eyebrow mb-1">{eyebrow}</p>}
        <Heading className={cn('text-balance text-foreground', HEADING[as])}>{title}</Heading>
        {note == null ? null : (
          <p className="mt-1 max-w-prose text-pretty text-sm leading-relaxed text-muted-foreground">{note}</p>
        )}
      </div>
      {right == null ? null : <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  )
}

export type { SectionHeaderProps }
