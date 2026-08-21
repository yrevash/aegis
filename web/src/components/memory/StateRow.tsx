import { Loader2 } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

/**
 * A quiet inline "loading…" row shared by the Memory panels.
 *
 * `role="status"` (which implies `aria-live="polite"`) so a screen reader is told
 * the panel is fetching rather than being left on a silent, empty card.
 */
export function LoadingRow({ label = 'Loading…' }: { label?: string }): ReactElement {
  return (
    <div role="status" className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden /> {label}
    </div>
  )
}

/**
 * A calm error row — never alarming, honest about the offline case.
 *
 * No "Could not load." prefix any more: `message` is a whole sentence now (see
 * `lib/api/apiError.ts`), and the prefix existed to make up for a `message` that used to
 * be `GET /memory/facts?subject=user%3A5 failed: 403 Forbidden`.
 */
export function ErrorRow({ message }: { message: string }): ReactElement {
  return (
    <div role="alert" className="py-4 text-sm text-destructive">
      {message}
    </div>
  )
}

/**
 * An empty-state row for a panel with nothing to show yet.
 *
 * `py-4`, not `py-8`. These three rows are what a sparse Memory card is *made*
 * of — "Recent updates", "Profile" and "Sessions" are one sentence each on a
 * young record — and 4rem of padding around that sentence is how a card ends up
 * mostly whitespace. The sentence is the content; the padding was not.
 */
export function EmptyRow({ children }: { children: ReactNode }): ReactElement {
  return <div className="py-4 text-sm text-muted-foreground">{children}</div>
}
