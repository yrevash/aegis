import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Card — a white panel on a hairline border.
 *
 * The soft diffuse shadow it used to carry is gone: DESIGN.md §4 keeps one
 * shadow token for genuinely floating layers — a popover, a dialog — and a
 * border everywhere else, because a page of shadowed rectangles reads as generic
 * SaaS chrome and says nothing about what sits above what.
 *
 * The corner is `rounded-lg`, which is `--radius` itself — 6px. It was
 * `rounded-2xl`, i.e. `--radius + 8px` = 14px, which is within rounding distance
 * of the 12px DESIGN.md §4 retired by name as "the SnowUI/SaaS middle ground".
 * The token exists so this is decided once; a card that opts up two steps from it
 * is the whole console opting up two steps, because every panel is this card.
 */
export function Card({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-card text-card-foreground',
        className,
      )}
    >
      {children}
    </div>
  )
}

/**
 * Card header — an eyebrow, the title, and one slot for a control.
 *
 * `description` is accepted for source-compatibility with existing call sites but
 * intentionally **not rendered**: the portals are for operators who already know
 * the surface, so per-card explainer prose is dropped to keep every page dense
 * and scannable.
 *
 * The title no longer truncates. `truncate` on a heading is a silent data loss —
 * "What the dependency table does not measure" became "What the dependency…" in
 * a narrow column, and the clipped half was the half that carried the meaning.
 * It wraps and balances instead, which costs a line and loses nothing.
 *
 * **The heading level is a prop, defaulting to `h2`.** It was hardcoded `h3`, so
 * every page that put a card under its `h1` announced `h1 -> h3` and skipped a
 * level — a screen-reader user navigating by heading hears a gap where a section
 * should be. Two redesign lanes reported it and neither could fix it from their
 * own directory. A card nested inside a section that already has an `h2` passes
 * `as="h3"`; the default is the common case, not the deepest one.
 */
export function CardHeader({
  title,
  eyebrow,
  actions,
  className,
  as: Heading = 'h2',
}: {
  title: ReactNode
  eyebrow?: ReactNode
  /** Accepted but not rendered — see the note above. */
  description?: ReactNode
  actions?: ReactNode
  className?: string
  /** The heading level this card's title occupies. Defaults to `h2`. */
  as?: 'h2' | 'h3' | 'h4'
}) {
  return (
    <div className={cn('flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-5 pt-5 md:px-6 md:pt-6', className)}>
      <div className="min-w-0">
        {eyebrow ? <p className="eyebrow mb-1">{eyebrow}</p> : null}
        <Heading className="text-pretty text-base leading-6 font-semibold text-foreground">{title}</Heading>
      </div>
      {actions ? <div className="shrink-0">{actions}</div> : null}
    </div>
  )
}

/** Card body — standard padding. */
export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('px-5 py-5 md:px-6 md:py-6', className)}>{children}</div>
}
