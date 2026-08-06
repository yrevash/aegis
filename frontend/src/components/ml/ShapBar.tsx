import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'
import type { ShapFeature } from '@/types/stream'

interface ShapBarProps {
  feature: ShapFeature
  /** Largest absolute contribution across the set, for scaling. */
  maxAbs: number
}

/**
 * One signed SHAP attribution, drawn as a bar diverging from a centre axis:
 * contributions that push the score up go right (healthy hue), down go left
 * (block hue). The bar length is proportional to the magnitude.
 */
export function ShapBar({ feature, maxAbs }: ShapBarProps): ReactElement {
  const positive = feature.contribution >= 0
  const widthPct = maxAbs > 0 ? (Math.abs(feature.contribution) / maxAbs) * 50 : 0

  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-2">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-mono text-[0.72rem] text-foreground">
          {feature.feature}
        </span>
        <span className="tabular font-mono text-[0.66rem] text-muted-foreground">
          = {feature.value}
        </span>
      </div>
      <div className="flex w-40 items-center">
        {/* Negative half (right-aligned toward centre) */}
        <div className="flex h-3.5 w-1/2 justify-end">
          {!positive && (
            <div
              className="h-full rounded-l-sm bg-block"
              style={{ width: `${(widthPct / 50) * 100}%` }}
            />
          )}
        </div>
        <div className="h-3.5 w-px bg-border" />
        {/* Positive half */}
        <div className="flex h-3.5 w-1/2">
          {positive && (
            <div
              className="h-full rounded-r-sm bg-ok"
              style={{ width: `${(widthPct / 50) * 100}%` }}
            />
          )}
        </div>
      </div>
      <span
        className={cn(
          'tabular col-start-2 -mt-0.5 text-right font-mono text-[0.64rem]',
          positive ? 'text-ok-ink' : 'text-block-ink',
        )}
      >
        {positive ? '+' : ''}
        {feature.contribution.toFixed(2)}
      </span>
    </div>
  )
}
