import type { LucideIcon } from 'lucide-react'

import { Figure, type FigureSize } from '@/components/primitives/Figure'
import { MiniTrend } from '@/components/shared/MiniTrend'
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
  valueSize = 'display',
  icon: Icon,
  tone = 'neutral',
  delta,
  trend,
  source,
  className,
}: {
  label: string
  /** The already-formatted figure. This tile sets type; it never formats. */
  value: string
  /**
   * The type step for the value. `display` (default) is the hero numeral this tile
   * exists for; `stat` is for a tile whose value is an **identifier** rather than a
   * number — a node name, a model id. Mono at 1.75rem is ~20px a glyph, so an
   * eleven-character id is 223px of unbreakable text and no KPI tile in a five-up row
   * is that wide. Setting the step is the honest fix; ellipsising a name is not.
   */
  valueSize?: FigureSize
  icon?: LucideIcon
  tone?: Signal
  delta?: { value: string; direction: 'up' | 'down' }
  /**
   * Chronological values, oldest → newest, drawn as a sparkline beneath the
   * figure. A KPI is a single sample; the shape it came from is the context a
   * reader needs to know whether that sample is news. Omit it when the figure
   * has no history — {@link MiniTrend} draws a quiet baseline below two finite
   * points rather than inventing a curve, but an absent series should be absent.
   */
  trend?: number[] | { value: number }[]
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
      {/* `flex-wrap`, because the delta chip does not shrink. Two-up at 390 and one step
          of text size, and this row had 108px for a 79px chip: the label column was
          crushed to **14px** and "Cost saved vs frontier" came out one broken glyph wide
          and 100px tall. The chip takes its own line when the pair will not fit. */}
      <div
        className={cn('flex flex-wrap items-end justify-between gap-x-3 gap-y-2', Icon && 'mt-5')}
      >
        {/* The last line of defence, not the plan: a value is meant to fit its tile
            (see `valueSize`). But this tile takes an arbitrary string and is drawn five
            across, so a value that does not fit must ellipsise inside its own card
            rather than paint over the border and push the page sideways — which is what
            a `guard_input` did at 1440, for eleven pixels of document overflow. */}
        <div className="min-w-0">
          <p className="text-sm text-muted-foreground">{label}</p>
          <span className="mt-2 block min-w-0" title={value}>
            <Figure truncate size={valueSize} className="text-foreground">
              {value}
            </Figure>
          </span>
        </div>
        {delta ? (
          <span
            className={cn(
              // `shrink-0` so the row wraps rather than crushing the label beside it;
              // `max-w-full` so that once it is on its own line it still cannot be wider
              // than the tile it sits in.
              'tabular flex max-w-full shrink-0 items-center gap-1 rounded-md px-2 py-0.5 font-mono text-xs font-medium',
              // Both chips wear the *ink* step, not the fill step. `--danger`
              // `#f04438` is a fill-weight red and measured **2.94:1** on its own
              // `bg-block/25` wash — the same defect the badge tones carried, in a
              // chip that reports a number moving the wrong way.
              delta.direction === 'up'
                ? 'bg-ok/20 text-[color:var(--ok-ink)]'
                : 'bg-block/25 text-[color:var(--block-ink)]',
            )}
          >
            <span aria-hidden>{delta.direction === 'up' ? '▲' : '▼'}</span>
            <span className="sr-only">{delta.direction === 'up' ? 'up' : 'down'}</span>
            {delta.value}
          </span>
        ) : null}
      </div>
      {trend == null ? null : (
        <div className="mt-4 -mx-1" aria-hidden>
          <MiniTrend data={trend} color={tone} height={36} />
        </div>
      )}
      {source == null ? null : <Receipt origin={source} className={cn(trend == null ? 'mt-4' : 'mt-3')} />}
    </div>
  )
}
