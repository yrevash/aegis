import type { ComponentProps, ReactNode } from 'react'

import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { cn } from '@/lib/utils'

/**
 * DataPanel — a card wrapped around tabular content, with the toolbar and the
 * scroll container that every such card was re-inventing.
 *
 * Twelve screens each built this by hand, and they disagreed in the two places
 * it matters. **Scroll** was the first: a wide table inside a plain `CardBody`
 * widens the card, which widens the page, and the result is a horizontally
 * scrolling *document* rather than a scrolling *table*. That defect does not
 * show at 1440px and is the whole experience at 390px, which is where this
 * product was last judged. Here the overflow is owned by an inner element that
 * cannot push the card wider, at every width.
 *
 * **The toolbar** was the second. Filters and a row count were variously placed
 * above the card, inside the header's action slot, or below the table; a reader
 * scanning three panels had to find the controls three times.
 *
 * It does not own the table markup — `ui/Table` does — so a panel can hold a
 * list, a chart, or a definition grid without pretending to be a table.
 */
export function DataPanel({
  title,
  eyebrow,
  as,
  actions,
  toolbar,
  footer,
  /**
   * Caps the scroll container's height so a long table scrolls *inside* the
   * panel instead of pushing the rest of the page below the fold. Omit for a
   * panel that should grow — a short, complete list reads better whole.
   */
  maxHeight,
  children,
  className,
  bodyClassName,
  ...props
}: {
  title: ReactNode
  eyebrow?: ReactNode
  /** Heading level, forwarded to {@link CardHeader}. Defaults to `h2` there. */
  as?: ComponentProps<typeof CardHeader>['as']
  /** One control in the header, aligned with the title. */
  actions?: ReactNode
  /** Filters, search, a row count — one row directly above the data. */
  toolbar?: ReactNode
  /** Pagination or a receipt, below the data and outside the scroll area. */
  footer?: ReactNode
  maxHeight?: number | string
  children: ReactNode
  className?: string
  bodyClassName?: string
} & Omit<ComponentProps<'div'>, 'title'>) {
  const scrolls = maxHeight != null
  return (
    <Card className={cn('flex flex-col', className)} {...props}>
      <CardHeader title={title} eyebrow={eyebrow} actions={actions} as={as} />
      <CardBody className={cn('flex min-h-0 flex-col gap-3', bodyClassName)}>
        {toolbar ? (
          <div className="flex flex-wrap items-center gap-2 [&>*]:min-w-0">{toolbar}</div>
        ) : null}
        <div
          // Two properties here are load-bearing and neither is obvious.
          //
          // `min-w-0` stops a wide child from widening the flex parent: a flex
          // item's default `min-width: auto` resolves to its content, so
          // `overflow-x-auto` alone silently does nothing.
          //
          // `relative` stops the *document* from growing to the clipped content's
          // height. A `static` scroll container still contributes its overflow to
          // the nearest positioned ancestor's scroll extent, so the panel scrolled
          // correctly at its own `maxHeight` while the page underneath it grew to
          // the full table — Jobs measured **10,948px tall with ~9,000px empty**,
          // and 2,232px with this one word added. Establishing a containing block
          // is what keeps the overflow inside the box that owns it.
          className="relative -mx-5 min-w-0 overflow-x-auto overscroll-x-contain px-5 md:-mx-6 md:px-6"
          style={scrolls ? { maxHeight, overflowY: 'auto' } : undefined}
          // A scrollable region must be reachable and announced; without these a
          // keyboard user cannot reach the rows that are off-screen.
          {...(scrolls ? { tabIndex: 0, role: 'group', 'aria-label': typeof title === 'string' ? title : undefined } : {})}
        >
          {children}
        </div>
        {footer ? <div className="flex flex-wrap items-center gap-3">{footer}</div> : null}
      </CardBody>
    </Card>
  )
}
