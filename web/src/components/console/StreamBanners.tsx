'use client'

import { CircleSlash, Inbox, Wallet } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

/** One notice row. */
function Notice({
  icon: Icon,
  tone,
  title,
  children,
}: {
  icon: LucideIcon
  tone: 'block' | 'ml' | 'risk'
  title: string
  children?: ReactNode
}): ReactElement {
  const toneClass =
    tone === 'block'
      ? 'border-block/50 bg-block/10 text-block-ink'
      : tone === 'ml'
        ? 'border-ml/50 bg-ml/10 text-ml-ink'
        : 'border-risk/50 bg-risk/10 text-risk-ink'
  return (
    <div className={cn('flex items-start gap-3 rounded-lg border px-4 py-3', toneClass)}>
      <Icon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">
        <p className="text-sm font-semibold">{title}</p>
        {children && <p className="mt-0.5 text-[0.8rem] leading-snug text-foreground/80">{children}</p>}
      </div>
    </div>
  )
}

/**
 * Terminal / governance notices surfaced from the run stream: a budget breach,
 * an abstain outcome, or a durable inbox enqueue. Rendered above the console
 * grid so the jury reads the honest outcome, not just the happy path.
 */
export function StreamBanners({ state }: { state: RunState }): ReactElement | null {
  const { budgetExceeded, abstained, approvalQueued } = state
  if (!budgetExceeded && !abstained && !approvalQueued) return null

  return (
    <div className="flex flex-col gap-2">
      {budgetExceeded && (
        <Notice icon={Wallet} tone="block" title="Budget exceeded — run stopped">
          {budgetExceeded.message}
          {budgetExceeded.limit != null && budgetExceeded.used != null && (
            <span className="ml-1 font-mono text-[0.72rem]">
              ({budgetExceeded.limit_type}: {budgetExceeded.used} / {budgetExceeded.limit})
            </span>
          )}
        </Notice>
      )}
      {abstained && (
        <Notice icon={CircleSlash} tone="ml" title="Insufficient confidence — did not act">
          {abstained.reason}
        </Notice>
      )}
      {approvalQueued && (
        <Notice icon={Inbox} tone="risk" title="Queued to the approvals inbox">
          {approvalQueued.action}
          {approvalQueued.assignee_tier && (
            <span className="ml-1 font-mono text-[0.72rem]">→ {approvalQueued.assignee_tier}</span>
          )}
        </Notice>
      )}
    </div>
  )
}