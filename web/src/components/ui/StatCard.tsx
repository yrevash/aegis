import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * StatCard / KpiTile — TailAdmin's metric card (icon tile + label + big number +
 * delta pill), restyled to our tokens: a soft signal-tinted icon tile, an
 * oversized near-black display number, and a green/red delta chip.
 */
export function StatCard({
  label,
  value,
  icon: Icon,
  tone = 'neutral',
  delta,
  className,
}: {
  label: string
  value: string
  icon?: LucideIcon
  tone?: 'neutral' | 'agent' | 'graph' | 'risk' | 'block' | 'ok' | 'ml'
  delta?: { value: string; direction: 'up' | 'down' }
  className?: string
}) {
  const tileTone: Record<string, string> = {
    neutral: 'bg-surface-2 text-foreground',
    agent: 'bg-agent/20 text-agent-ink',
    graph: 'bg-graph/20 text-graph-ink',
    risk: 'bg-risk/25 text-risk-ink',
    block: 'bg-block/25 text-block-ink',
    ok: 'bg-ok/20 text-ok-ink',
    ml: 'bg-ml/20 text-ml-ink',
  }

  return (
    <div
      className={cn(
        'rounded-2xl border border-border bg-card p-5 shadow-card transition-shadow hover:shadow-hover md:p-6',
        className,
      )}
    >
      {Icon ? (
        <div className={cn('flex h-11 w-11 items-center justify-center rounded-xl', tileTone[tone])}>
          <Icon className="size-5" />
        </div>
      ) : null}
      <div className={cn('flex items-end justify-between', Icon && 'mt-5')}>
        <div className="min-w-0">
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="t-metric tabular mt-2 text-foreground">{value}</p>
        </div>
        {delta ? (
          <span
            className={cn(
              'flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium tabular',
              delta.direction === 'up'
                ? 'bg-ok/20 text-[color:var(--success)]'
                : 'bg-block/25 text-[color:var(--danger)]',
            )}
          >
            {delta.direction === 'up' ? '▲' : '▼'} {delta.value}
          </span>
        ) : null}
      </div>
    </div>
  )
}
