'use client'

import { useId, useState, type ComponentProps, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'

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
 *
 * ## `collapsible` — why the primitive owns it
 *
 * The whole platform is demonstrated in ten to fifteen minutes, and a reviewer who
 * lands on a screen has seconds to decide what it is. Forty rows of a correct table
 * spend those seconds. So a listing opens **closed**: one bar carrying its title,
 * its row count and one key figure (DESIGN.md §4).
 *
 * It lives here rather than in 33 call sites for the same reason `Absence` was fixed
 * in `Receipt` — one edit, every panel, no call-site churn — and because a
 * hand-rolled disclosure gets the accessibility wrong every time. The rows are
 * **collapsed, never `display:none`**: they stay in the tree, the trigger is a real
 * button with `aria-expanded`, and hover is a convenience layered on top of a
 * control that works from the keyboard alone.
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
  collapsible = false,
  summary,
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
  /**
   * Open closed; hover reveals; click pins.
   *
   * Hover alone would be a trap — the content vanishes the moment the pointer
   * leaves, so it cannot be read, scrolled or copied. The click is what makes the
   * disclosure usable; the hover is what makes it feel instant.
   */
  collapsible?: boolean
  /**
   * The one line the closed bar carries beside the title — a count and a key
   * figure, e.g. `10 rows · 7 ingested`.
   *
   * Without it the bar says only that a panel exists, and a reviewer cannot tell a
   * populated panel from an empty one without hovering, which defeats the point.
   */
  summary?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
} & Omit<ComponentProps<'div'>, 'title'>) {
  const scrolls = maxHeight != null
  const bodyId = useId()

  // `pinned` is the click; `peeking` is the hover. Kept apart on purpose: a panel
  // opened by hover must NOT close when the pointer wanders out of a panel the
  // reader deliberately pinned, and a pinned panel must not re-close on mouseleave.
  const [pinned, setPinned] = useState(false)
  const [peeking, setPeeking] = useState(false)
  const open = !collapsible || pinned || peeking

  const label = typeof title === 'string' ? title : undefined

  return (
    <Card
      className={cn('flex flex-col', className)}
      {...(collapsible
        ? {
            onMouseEnter: () => setPeeking(true),
            onMouseLeave: () => setPeeking(false),
          }
        : {})}
      {...props}
    >
      <CardHeader
        title={title}
        eyebrow={eyebrow}
        as={as}
        actions={
          collapsible ? (
            <div className="flex items-center gap-3">
              {summary ? (
                <span className="text-xs tabular-nums text-muted-foreground">{summary}</span>
              ) : null}
              {actions}
              <button
                type="button"
                onClick={() => setPinned((v) => !v)}
                aria-expanded={open}
                aria-controls={bodyId}
                // Named, because "expand" alone tells a screen-reader user nothing
                // about WHICH of six panels on the page is about to open.
                aria-label={`${pinned ? 'Collapse' : 'Expand'}${label ? ` ${label}` : ''}`}
                className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                <ChevronRight
                  className={cn(
                    'size-4 transition-transform duration-[--dur-fast]',
                    open && 'rotate-90',
                  )}
                  aria-hidden
                />
              </button>
            </div>
          ) : (
            actions
          )
        }
      />
      <CardBody
        id={bodyId}
        className={cn(
          'flex min-h-0 flex-col gap-3',
          // Collapsed: zero height, no padding, and `invisible` rather than
          // `hidden` — the rows stay in the accessibility tree and the layout is
          // already measured, so expanding is instant and nothing is lost to a
          // screen reader. `display:none` would do neither.
          collapsible && !open && 'invisible h-0 overflow-hidden !py-0 opacity-0',
          collapsible && 'transition-opacity duration-[--dur-fast]',
          bodyClassName,
        )}
        {...(collapsible && !open ? { 'aria-hidden': true } : {})}
      >
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
