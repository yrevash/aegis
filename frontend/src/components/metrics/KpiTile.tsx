import type { LucideIcon } from 'lucide-react'
import type { ReactElement } from 'react'

import { SIGNALS, type Signal } from '@/config/signals'
import { cn } from '@/lib/utils'

interface KpiTileProps {
  label: string
  value: string
  /** Optional sub-line (context, target, or delta). */
  sub?: string
  icon?: LucideIcon
  signal?: Signal
  /** Optional 0..1 meter rendered under the value. */
  meter?: number
  /** Marks the value as an illustrative sample (not live backend data). */
  sample?: boolean
  /** Optional soft SnowUI card tint behind the tile (else plain white card). */
  tint?: 'blue' | 'purple'
}

/** A single KPI read-out tile with an optional meter, coloured by subsystem. */
export function KpiTile({
  label,
  value,
  sub,
  icon: Icon,
  signal = 'neutral',
  meter,
  sample = false,
  tint,
}: KpiTileProps): ReactElement {
  const token = SIGNALS[signal]
  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-lg p-4',
        tint === 'blue'
          ? 'bg-tint-blue'
          : tint === 'purple'
            ? 'bg-tint-purple'
            : 'border border-border/80 bg-card',
      )}
    >
      <div className="flex items-center gap-2">
        {Icon && (
          <span className={cn('grid size-6 place-items-center rounded-md', token.bg)}>
            <Icon className={cn('size-3.5', token.text)} />
          </span>
        )}
        <span className="eyebrow">{label}</span>
        {sample && (
          <span className="eyebrow ml-auto rounded-sm border border-border/70 px-1.5 py-0.5 text-[0.58rem] text-muted-foreground">
            sample
          </span>
        )}
      </div>
      <p className="tabular font-display text-[1.75rem] leading-none font-semibold text-foreground">
        {value}
      </p>
      {meter != null && (
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{ width: `${Math.round(meter * 100)}%`, background: token.hex }}
          />
        </div>
      )}
      {sub && <p className="font-mono text-[0.68rem] text-muted-foreground">{sub}</p>}
    </div>
  )
}
