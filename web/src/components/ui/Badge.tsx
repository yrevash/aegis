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
 *
 * Squared to the radius token rather than `rounded-full`. Some console screens
 * carry a dozen of these in a single table column, and a dozen pills in a column
 * is the "pill overuse" DESIGN.md §8 names — the shape stops meaning "status"
 * once everything countable wears it. A 4px corner still reads as a chip and
 * sits square against the table rules it lives between.
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
        'inline-flex items-center justify-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium',
        signal.bg,
        // `neutral` is the one tone whose signal ink is not legible on its own
        // fill: `--muted-foreground` `#64748d` is 4.75:1 on white — which is where
        // the rest of the signal map spends it — but only **4.37:1** on
        // `--surface-2`, and this text is 12px. The losing `primitives/badge`
        // spelled this same chip `bg-surface-2 text-foreground`, so foreground ink
        // is what these badges have always looked like; it measures 15.43:1.
        tone === 'neutral' ? 'text-foreground' : signal.text,
        className,
      )}
    >
      {children}
    </span>
  )
}
