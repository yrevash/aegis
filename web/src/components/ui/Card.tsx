'use client'

import { ChevronRight } from 'lucide-react'
import { useState, type ComponentProps, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * Card — a white panel on a hairline border, and the **only** card in the app.
 *
 * There were two. `primitives/card.tsx` shipped a `rounded-xl` `flex flex-col`
 * surface with `CardHeader`/`CardTitle`/`CardContent`, and roughly half the
 * screens used it while the other half used this one — which is a guarantee of
 * visual incoherence that no amount of per-screen polish can reach. That file
 * is deleted; every call site is here.
 *
 * It takes the rest of the `div` props, which the losing card did by being a
 * `div`. A tile that is focusable (`tabIndex`), a panel that is a click target,
 * a card that names itself for a test — those are card call sites, and a card
 * that cannot express them sends its author back to raw markup.
 *
 * The soft diffuse shadow it used to carry is gone: DESIGN.md §4 keeps one
 * shadow token for genuinely floating layers — a popover, a dialog — and a
 * border everywhere else, because a page of shadowed rectangles reads as generic
 * SaaS chrome and says nothing about what sits above what.
 *
 * The corner is `rounded-lg`, which is `--radius` itself — **8px** since v2.
 * The verified card radii across the nine surveyed systems are 0/4/6/6/6/8/8/8/12
 * (DESIGN.md §1): nothing rounds a data card past 16px, and generous rounding is
 * the marketing-page dialect. The losing card was `rounded-xl`, one step up. The
 * token exists so this is decided once; a card that opts up from it is the whole
 * console opting up, because every panel is this card.
 */
export function Card({
  children,
  className,
  collapsible = false,
  summary,
  ...props
}: ComponentProps<'div'> & {
  /**
   * Open closed; hover reveals; click pins. The same contract as `DataPanel`
   * (DESIGN.md §4), available to any card without restructuring it.
   *
   * `DataPanel` owns its header and body, so it can place the control directly.
   * A plain `Card` receives them as children, and rewriting forty call sites from
   * `Card`+`CardHeader`+`CardBody` into `DataPanel` is a structural edit that
   * silently breaks the ones with a nested card or a non-self-closing header —
   * measured: a mechanical transform of four such files produced a file that did
   * not parse.
   *
   * So the collapse is applied from the OUTSIDE, through the `data-slot` hooks the
   * header and body already carry. No child is inspected, cloned or moved.
   */
  collapsible?: boolean
  /** The one line the closed bar carries — a count and a key figure. */
  summary?: ReactNode
}) {
  const [pinned, setPinned] = useState(false)
  const [peeking, setPeeking] = useState(false)
  const open = !collapsible || pinned || peeking

  return (
    <div
      data-slot="card"
      data-open={open ? 'true' : 'false'}
      className={cn(
        'rounded-lg border border-border bg-card text-card-foreground',
        collapsible && 'relative',
        // Room for the control, so it never lands on top of the header's own actions.
        collapsible && '[&>[data-slot=card-header]]:pr-14',
        collapsible &&
          !open &&
          // `invisible` + zero height, never `hidden`: the content stays in the
          // accessibility tree and keeps its measured layout, so expanding is instant
          // and a screen-reader user loses nothing.
          '[&>[data-slot=card-body]]:invisible [&>[data-slot=card-body]]:h-0 [&>[data-slot=card-body]]:overflow-hidden [&>[data-slot=card-body]]:!py-0',
        className,
      )}
      {...(collapsible
        ? { onMouseEnter: () => setPeeking(true), onMouseLeave: () => setPeeking(false) }
        : {})}
      {...props}
    >
      {collapsible ? (
        <div className="absolute right-4 top-4 z-10 flex items-center gap-2">
          {summary ? (
            <span className="text-xs tabular-nums text-muted-foreground">{summary}</span>
          ) : null}
          <button
            type="button"
            onClick={() => setPinned((v) => !v)}
            aria-expanded={open}
            aria-label={open ? 'Collapse this panel' : 'Expand this panel'}
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <ChevronRight
              className={cn('size-4 transition-transform duration-[--dur-fast]', open && 'rotate-90')}
              aria-hidden
            />
          </button>
        </div>
      ) : null}
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
 *
 * **It owns the gap below itself.** It used to carry no bottom padding at all,
 * and call sites cancelled the body's top padding with `pt-0` instead — which
 * does not work. `CardBody`'s padding is `py-5 md:py-6`, and an unprefixed
 * `pt-0` is emitted *before* `md:py-6` in the sheet, so from 768px up the
 * override was silently ignored, while below it the title sat flush against the
 * body with no gap at all. One rule in `globals.css` collapses the body's top
 * padding after a header at every width, so `pt-*` on a body means what it says.
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
    <div
      data-slot="card-header"
      className={cn(
        'flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-5 pt-5 pb-4 md:px-6 md:pt-6',
        className,
      )}
    >
      <div className="min-w-0">
        {eyebrow ? <p className="eyebrow mb-1">{eyebrow}</p> : null}
        <Heading className="text-pretty text-base leading-6 font-semibold text-foreground">{title}</Heading>
      </div>
      {/*
       * Not `shrink-0`. A header's actions slot is usually one button, where refusing to
       * shrink is right — but several callers put a `flex-wrap` row of filter chips in
       * here, and `shrink-0` pins that row to its single-line width so it can never
       * actually wrap. It just runs off the page: `devops/audit` measured
       * `scrollWidth 753` against a 390px viewport, a 363px horizontal scroll on the
       * whole document, from six lens chips that had every opportunity to stack.
       *
       * Plain `flex-shrink: 1` with the default `min-width: auto` is the fix: a flex item
       * still will not shrink below its min-content width, so a lone nowrap button is
       * unaffected, while a wrapping row is free to become two lines.
       */}
      {actions ? <div>{actions}</div> : null}
    </div>
  )
}

/**
 * Card body — standard padding.
 *
 * It takes the rest of the `div` props for the same reason {@link Card} does:
 * the panel that scrolls its own log needs a `ref` on the element that scrolls,
 * and a body that could not carry one forced its call site back out to raw
 * markup — which is how a second card system starts.
 */
export function CardBody({ children, className, ...props }: ComponentProps<'div'>) {
  return (
    <div data-slot="card-body" className={cn('px-5 py-5 md:px-6 md:py-6', className)} {...props}>
      {children}
    </div>
  )
}
