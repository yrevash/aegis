import { ArrowDown, ArrowUp, Minus } from 'lucide-react'
import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

export type DeltaTone = 'good' | 'bad' | 'neutral'
export type DeltaDirection = 'up' | 'down'

interface StatDeltaProps {
  /** Magnitude shown next to the arrow (already the delta, not the total). */
  value: number
  /** Arrow direction; inferred from the sign of `value` when omitted. */
  direction?: DeltaDirection
  /**
   * Colour tone — decoupled from direction, because "down" is not always bad
   * (e.g. latency, cost). Defaults: up → good, down → bad.
   */
  tone?: DeltaTone
  /** Trailing text, e.g. "%" or "vs last month". */
  suffix?: string
}

const TONE_CLASS: Record<DeltaTone, string> = {
  good: 'text-success',
  bad: 'text-danger',
  neutral: 'text-muted-foreground',
}

/** The small ▲/▼ delta chip used on KPI tiles and heroes. */
export function StatDelta({ value, direction, tone, suffix }: StatDeltaProps): ReactElement {
  const dir: DeltaDirection = direction ?? (value < 0 ? 'down' : 'up')
  const resolvedTone: DeltaTone = tone ?? (dir === 'up' ? 'good' : 'bad')
  const Icon = value === 0 ? Minus : dir === 'up' ? ArrowUp : ArrowDown
  const magnitude = Math.abs(value)

  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 font-mono text-xs font-medium tabular',
        TONE_CLASS[resolvedTone],
      )}
    >
      <Icon aria-hidden className="size-3" />
      <span>
        <span className="sr-only">{dir === 'up' ? 'up ' : 'down '}</span>
        {magnitude.toLocaleString()}
        {suffix ? <span className="ml-0.5 text-muted-foreground">{suffix}</span> : null}
      </span>
    </span>
  )
}
