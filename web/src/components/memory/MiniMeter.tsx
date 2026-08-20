import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

interface MiniMeterProps {
  /** Fraction to fill, 0..1 (clamped). A genuine single-value gauge (§ principle 6). */
  value: number
  /** Bar hue as a CSS colour / var (default the ML ink used for confidence). */
  hex?: string
  /** Track height in px (default 6). */
  height?: number
  className?: string
}

/**
 * A tiny single-value fill bar — the calm, chart-free meter used for fact
 * confidence and the recall sub-scores. Kept free of recharts so it renders in
 * SSR/tests and stays visually quiet inside dense rows.
 */
export function MiniMeter({ value, hex = 'var(--blue-100)', height = 6, className }: MiniMeterProps): ReactElement {
  const pct = Math.max(0, Math.min(100, Math.round((Number.isFinite(value) ? value : 0) * 100)))
  return (
    <div
      className={cn('w-full overflow-hidden rounded-full bg-surface-2', className)}
      style={{ height }}
      role="meter"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="h-full rounded-full transition-[width] duration-700 ease-out"
        style={{ width: `${pct}%`, background: hex }}
      />
    </div>
  )
}
