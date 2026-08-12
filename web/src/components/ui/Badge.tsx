import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Badge — TailAdmin's rounded-full pill, recoloured onto the Aegis signal
 * taxonomy (soft fill + readable ink) instead of TailAdmin's brand blue. Each
 * tone maps to a subsystem of the trust taxonomy.
 */
export type BadgeTone =
  | 'neutral'
  | 'agent'
  | 'graph'
  | 'risk'
  | 'block'
  | 'ok'
  | 'ml'

const TONE: Record<BadgeTone, string> = {
  neutral: 'bg-surface-2 text-muted-foreground',
  agent: 'bg-agent/20 text-agent-ink',
  graph: 'bg-graph/20 text-graph-ink',
  risk: 'bg-risk/25 text-risk-ink',
  block: 'bg-block/25 text-block-ink',
  ok: 'bg-ok/20 text-ok-ink',
  ml: 'bg-ml/20 text-ml-ink',
}

export function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode
  tone?: BadgeTone
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center justify-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium',
        TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}
