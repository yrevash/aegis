import type { ReactNode } from 'react'

import { SIGNALS, type Signal } from '@/config/signals'
import { cn } from '@/lib/utils'

/**
 * Badge — a small status pill on the signal taxonomy (soft step + readable ink).
 *
 * The tone map used to be a private copy of the signal palette, which is how a
 * console ends up with two spellings of the same status. It reads `SIGNALS`
 * now, so a badge can only ever wear a token that exists.
 *
 * A status badge always ships with a **word**, never colour alone: `risk`,
 * `block` and `ok` fail CVD separation against each other by design, and the
 * label is what actually distinguishes them (DESIGN.md §2).
 */
export type BadgeTone = Signal

export function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode
  tone?: BadgeTone
  className?: string
}) {
  const signal = SIGNALS[tone]
  return (
    <span
      className={cn(
        'inline-flex items-center justify-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium',
        signal.bg,
        signal.text,
        className,
      )}
    >
      {children}
    </span>
  )
}
