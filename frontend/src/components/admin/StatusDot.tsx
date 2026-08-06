import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

interface StatusDotProps {
  /** Healthy / enabled → green dot; otherwise a muted amber dot. */
  ok: boolean
  /** The status word shown beside the dot (state never rides on colour alone). */
  label: string
  className?: string
}

/**
 * A calm status marker for the admin tables: a coloured dot paired with its
 * word, so an auditor scans state down a column without a heavy badge on every
 * row. Colour is always backed by the label (accessibility §1.8).
 */
export function StatusDot({ ok, label, className }: StatusDotProps): ReactElement {
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <span
        aria-hidden
        className="inline-block size-2 shrink-0 rounded-full"
        style={{ background: ok ? 'var(--ok-ink)' : 'var(--risk-ink)' }}
      />
      <span className={cn('text-[0.8125rem]', ok ? 'text-foreground' : 'text-risk-ink')}>{label}</span>
    </span>
  )
}
