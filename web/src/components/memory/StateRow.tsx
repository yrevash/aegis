import { Loader2 } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

/** A quiet inline "loading…" row shared by the Memory panels. */
export function LoadingRow({ label = 'Loading…' }: { label?: string }): ReactElement {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" /> {label}
    </div>
  )
}

/** A calm error row — never alarming, honest about the offline case. */
export function ErrorRow({ message }: { message: string }): ReactElement {
  return (
    <div className="py-8 text-sm text-destructive">
      Could not load. {message}
    </div>
  )
}

/** An empty-state row for a panel with nothing to show yet. */
export function EmptyRow({ children }: { children: ReactNode }): ReactElement {
  return <div className="py-8 text-sm text-muted-foreground">{children}</div>
}
