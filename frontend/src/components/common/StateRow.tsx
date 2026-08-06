import { Loader2 } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

/** A quiet inline "loading…" row used inside cards on the new surfaces. */
export function LoadingRow({ label = 'Loading…' }: { label?: string }): ReactElement {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" />
      {label}
    </div>
  )
}

/** A non-alarming error row (backend unreachable / request failed). */
export function ErrorRow({ message }: { message: string }): ReactElement {
  return <div className="py-8 text-sm text-destructive">Could not load. {message}</div>
}

/** An honest empty state with an optional icon. */
export function EmptyRow({ children }: { children: ReactNode }): ReactElement {
  return <div className="py-8 text-sm text-muted-foreground">{children}</div>
}
