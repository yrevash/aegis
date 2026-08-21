import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Table — TailAdmin's basic table shell restyled to our tokens: a hairline
 * divider grid, muted mono-ish column heads, tabular figures. Wrapped in an
 * overflow-x container so wide tables scroll without bursting the layout.
 */
export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn('w-full min-w-full border-collapse text-left text-sm', className)}>
        {children}
      </table>
    </div>
  )
}

/**
 * The header row. **Takes `TH` children directly — never wrap them in a `TR`.**
 *
 * This emits its own `<tr>`, so `<THead><TR>…</TR></THead>` renders a `<tr>` inside a
 * `<tr>`: invalid HTML, and React reports it as a hydration error at every viewport.
 * `health/PipelineHealthView.tsx` did exactly that in three tables and logged the error
 * on every `devops/stack` load; every other caller in the codebase already passes `TH`
 * directly, so the shape is right and the note is here to keep it that way.
 */
export function THead({ children }: { children: ReactNode }) {
  return (
    <thead className="border-b border-border">
      <tr>{children}</tr>
    </thead>
  )
}

export function TH({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <th
      className={cn(
        'px-4 py-3 text-xs font-medium uppercase tracking-wide text-muted-foreground',
        className,
      )}
    >
      {children}
    </th>
  )
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody className="divide-y divide-border">{children}</tbody>
}

export function TR({ children, className }: { children: ReactNode; className?: string }) {
  return <tr className={cn('transition-colors hover:bg-surface-2/60', className)}>{children}</tr>
}

export function TD({ children, className }: { children: ReactNode; className?: string }) {
  return <td className={cn('px-4 py-3 text-foreground', className)}>{children}</td>
}
