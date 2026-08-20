import { CircleAlert, type LucideIcon } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { errorSentence } from '@/lib/api/apiError'
import { cn } from '@/lib/utils'

interface EmptyStateProps {
  /** A quiet mark for the region. Optional — a label it duplicates is noise. */
  icon?: LucideIcon
  /** What this region holds when it is not empty. Sentence case, no full stop. */
  title: string
  /** Why it is empty right now. One sentence, in the active voice. */
  body: string
  /**
   * The one action that fills it — a button, a link. One, not three: a person
   * looking at an empty region needs a next move, not a menu.
   */
  action?: ReactNode
  className?: string
}

/**
 * An empty region, explained.
 *
 * DESIGN.md §5: an empty state is an instruction, not a shrug. Three things,
 * always in this order — what this holds, why it is empty, the one action that
 * fills it. The console currently says "No tenants yet." and stops, which
 * leaves a reader unable to tell an empty table from a broken one.
 *
 * @example
 * <EmptyState
 *   icon={Building2}
 *   title="No tenants yet"
 *   body="Every tenant on this platform appears here, newest first."
 *   action={<Button onClick={openCreate}>Create the first tenant</Button>}
 * />
 */
export function EmptyState({
  icon: Icon,
  title,
  body,
  action,
  className,
}: EmptyStateProps): ReactElement {
  return (
    <div
      data-slot="empty-state"
      className={cn(
        'flex flex-col items-start gap-2 rounded-md border border-dashed border-border px-4 py-8',
        className,
      )}
    >
      {Icon == null ? null : <Icon className="size-5 text-muted-foreground" aria-hidden />}
      <p className="text-sm font-semibold text-balance text-foreground">{title}</p>
      <p className="max-w-prose text-pretty text-sm leading-relaxed text-muted-foreground">{body}</p>
      {action == null ? null : <div className="mt-2">{action}</div>}
    </div>
  )
}

interface ErrorStateProps {
  /** Whatever was caught. The server's own sentence is read out of it. */
  error: unknown
  /**
   * The sentence for a failure that carries no words of its own. Name what did
   * not happen and what to do. Never "something went wrong".
   */
  fallback?: string
  /** Wire this and a Retry appears. Omit it when there is nothing to retry. */
  retry?: () => void
  className?: string
}

/**
 * A failed region, in the server's own words.
 *
 * DESIGN.md §7: server refusals render the server's own sentence via
 * `apiError.ts`, never "something went wrong". `ApiError` has already done the
 * work — it carries a sentence in `message` and keeps the route out of it — so
 * this only has to get out of the way and render it.
 *
 * Deliberately not alarming: `role="status"` rather than `role="alert"`, and no
 * red fill. A panel that could not load is a fact, not an emergency, and a
 * console that shouts at every 500 teaches an operator to ignore it.
 */
export function ErrorState({
  error,
  fallback,
  retry,
  className,
}: ErrorStateProps): ReactElement {
  return (
    <div
      data-slot="error-state"
      role="status"
      className={cn(
        'flex items-start gap-2 rounded-md border border-border bg-surface-2/40 px-4 py-6',
        className,
      )}
    >
      <CircleAlert className="mt-0.5 size-4 shrink-0 text-block-ink" aria-hidden />
      <div className="min-w-0 space-y-2">
        <p className="max-w-prose text-pretty text-sm leading-relaxed text-foreground">
          {errorSentence(error, fallback)}
        </p>
        {retry == null ? null : (
          <button
            type="button"
            onClick={retry}
            className="touch-manipulation rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-foreground transition-colors outline-none hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            Retry
          </button>
        )}
      </div>
    </div>
  )
}

interface LoadingStateProps {
  /**
   * How many placeholder rows to draw. Match the shape of what is loading —
   * a skeleton that is the wrong height moves the page twice instead of once.
   */
  rows?: number
  /** What a screen reader is told while the region is busy. Ends with `…`. */
  label?: string
  className?: string
}

/**
 * A region that is still loading, drawn as its own shape.
 *
 * A centred spinner tells a reader that something is happening and nothing about
 * what; worse, it occupies a different amount of space than the content it
 * precedes, so the page jumps when the data lands. Placeholder rows the size of
 * the real rows do not.
 *
 * The pulse is decorative and the global `prefers-reduced-motion` block in
 * `globals.css` stops it; the `role="status"` line is what actually carries the
 * information, so nothing is lost when the animation is.
 */
export function LoadingState({
  rows = 3,
  label = 'Loading…',
  className,
}: LoadingStateProps): ReactElement {
  return (
    <div
      data-slot="loading-state"
      role="status"
      aria-live="polite"
      aria-busy="true"
      className={cn('space-y-2 py-2', className)}
    >
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          aria-hidden
          className={cn(
            'h-8 animate-pulse rounded-md bg-surface-2',
            // The last row runs short, the way a real last row usually does.
            index === rows - 1 && rows > 1 && 'w-2/3',
          )}
        />
      ))}
    </div>
  )
}

export type { EmptyStateProps, ErrorStateProps, LoadingStateProps }
