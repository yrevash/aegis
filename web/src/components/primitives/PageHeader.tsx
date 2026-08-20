import type { ReactElement, ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface PageHeaderProps {
  /** The small mono tag above the title. Two or three words, lower case. */
  eyebrow?: ReactNode
  /** The page title. Sentence case, and there is exactly one per screen. */
  title: ReactNode
  /**
   * One line of context, and only when the title alone cannot carry it. Most
   * screens do not need this — a paragraph explaining a mechanism belongs in an
   * `InfoTip`, and a paragraph explaining a number belongs in a `Receipt`
   * (DESIGN.md §4).
   */
  note?: ReactNode
  /** The screen's controls — a filter, a refresh, one primary action. */
  actions?: ReactNode
  className?: string
}

/**
 * The one page title treatment, for all twelve sections.
 *
 * Three of them were in concurrent use. Most screens reached for
 * {@link SectionHeader} with `as="h1"`, which is correct but is the *section*
 * component doing a page's job. Seven screens hand-rolled an `eyebrow` `<p>`
 * over an `<h1 className="t-hero">`, and `.t-hero` is a
 * `clamp(2.25rem → 3.5rem)` — up to **56px**, which is the marketing dialect and
 * is the oversized heading the phone test caught. The rest improvised markup.
 *
 * The title here is **28px / 32 / -0.02em / 600** — the `display` step of
 * DESIGN.md §3, and the same ceiling Atlassian's `font.metric.*` ramp caps at.
 * It is not configurable, because the entire point of the component is that a
 * screen does not get to choose how loud its own title is.
 *
 * `.t-hero` survives for the landing page, which is a different medium with a
 * different job. It is retired from every product screen.
 *
 * @example
 * <PageHeader eyebrow="Whisper · rails" title="Voice" />
 * <PageHeader eyebrow="screen · then model" title="Vision" actions={<RunButton />} />
 */
export function PageHeader({
  eyebrow,
  title,
  note,
  actions,
  className,
}: PageHeaderProps): ReactElement {
  return (
    <div
      data-slot="page-header"
      className={cn('flex flex-wrap items-start justify-between gap-x-4 gap-y-3', className)}
    >
      <div className="min-w-0">
        {eyebrow == null ? null : <p className="eyebrow mb-1">{eyebrow}</p>}
        <h1 className="text-balance text-[1.75rem] leading-8 font-semibold tracking-[-0.02em] text-foreground">
          {title}
        </h1>
        {note == null ? null : (
          <p className="mt-1.5 max-w-prose text-pretty text-sm leading-relaxed text-muted-foreground">
            {note}
          </p>
        )}
      </div>
      {actions == null ? null : (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
      )}
    </div>
  )
}

export type { PageHeaderProps }
