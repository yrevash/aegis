import type { LucideIcon } from 'lucide-react'

import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { SIGNALS, type Signal } from '@/config/signals'
import { cn } from '@/lib/utils'

/**
 * StatCard / KpiTile — an icon chip, a label, one figure, and where it came from.
 *
 * Two things changed when the design system landed. The number is set in
 * JetBrains Mono with tabular figures through {@link Figure}, because a KPI that
 * reflows sideways as it ticks reads as a number that is still settling
 * (DESIGN.md §3). And the tile can carry a {@link Receipt}: a governance figure
 * whose origin is not on the tile is a figure a reader has to take on trust,
 * which is the one thing this product refuses to ask for.
 *
 * The tone map is no longer a fourth private copy of the signal palette — it
 * reads `SIGNALS`, so a tint can only ever be a token that exists.
 */
export function StatCard({
  label,
  value,
  icon: Icon,
  tone = 'neutral',
  delta,
  source,
  className,
}: {
  label: string
  /** The already-formatted figure. This tile sets type; it never formats. */
  value: string
  icon?: LucideIcon
  tone?: Signal
  delta?: { value: string; direction: 'up' | 'down' }
  /** Where the figure came from. Omit only when the figure has no origin. */
  source?: string
  className?: string
}) {
  const signal = SIGNALS[tone]

  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-card p-5 transition-colors duration-[--dur-fast] hover:border-input md:p-6',
        className,
      )}
    >
      {Icon ? (
        <div
          className={cn(
            'flex size-10 items-center justify-center rounded-md',
            signal.bg,
            signal.text,
          )}
        >
          <Icon className="size-5" aria-hidden />
        </div>
      ) : null}
      <div className={cn('flex items-end justify-between gap-3', Icon && 'mt-5')}>
        <div className="min-w-0">
          <p className="text-sm text-muted-foreground">{label}</p>
          <Figure size="display" className="mt-2 text-foreground">
            {value}
          </Figure>
        </div>
        {delta ? (
          <span
            className={cn(
              'tabular flex items-center gap-1 rounded-md px-2 py-0.5 font-mono text-xs font-medium',
              delta.direction === 'up'
                ? 'bg-ok/20 text-[color:var(--success)]'
                : 'bg-block/25 text-[color:var(--danger)]',
            )}
          >
            <span aria-hidden>{delta.direction === 'up' ? '▲' : '▼'}</span>
            <span className="sr-only">{delta.direction === 'up' ? 'up' : 'down'}</span>
            {delta.value}
          </span>
        ) : null}
      </div>
      {source == null ? null : <Receipt origin={source} className="mt-4" />}
    </div>
  )
}
