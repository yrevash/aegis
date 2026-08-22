'use client'

import { Inbox, Wallet } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

/**
 * The two shapes a cap comes in, built once at module scope.
 *
 * `limit_type` is `usd`, `tokens`, `rpm` or `tpm` — a dollar cap and a token cap are not
 * the same kind of number and were both being printed raw, so a $12.5 cap rendered as
 * `12.5` beside a token cap rendered as `40000`. Neither read as a governance figure.
 */
const USD = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
})
const COUNT = new Intl.NumberFormat('en-US')

/** A cap or a usage figure, in the unit the cap is actually denominated in. */
function formatCap(limitType: string, value: number): string {
  return limitType === 'usd' ? USD.format(value) : COUNT.format(value)
}

/** One notice row. */
function Notice({
  icon: Icon,
  tone,
  title,
  children,
}: {
  icon: LucideIcon
  tone: 'block' | 'risk'
  title: string
  children?: ReactNode
}): ReactElement {
  const toneClass =
    tone === 'block'
      ? 'border-block/50 bg-block/10 text-block-ink'
      : 'border-risk/50 bg-risk/10 text-risk-ink'
  return (
    <div
      // A run stopping is not a thing to discover by scrolling. `alert` for the terminal
      // one, `status` for the one that only says where the decision went.
      role={tone === 'block' ? 'alert' : 'status'}
      className={cn('flex min-w-0 items-start gap-3 rounded-lg border px-4 py-3', toneClass)}
    >
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">
        <p className="text-sm font-semibold">{title}</p>
        {children && (
          <p className="mt-0.5 text-[0.8rem] leading-snug break-words text-foreground/80">
            {children}
          </p>
        )}
      </div>
    </div>
  )
}

/**
 * Terminal / governance notices surfaced from the run stream: a budget breach or
 * a durable inbox enqueue. Rendered above the console grid so the jury reads the
 * honest outcome, not just the happy path.
 */
export function StreamBanners({ state }: { state: RunState }): ReactElement | null {
  const { budgetExceeded, approvalQueued } = state
  if (!budgetExceeded && !approvalQueued) return null

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {budgetExceeded && (
        <Notice icon={Wallet} tone="block" title="Budget exceeded — run stopped">
          {budgetExceeded.message}
          {budgetExceeded.limit != null && budgetExceeded.used != null && (
            <span className="ml-1 whitespace-nowrap">
              ({budgetExceeded.limit_type}:{' '}
              <Figure className="text-[0.72rem]">
                {formatCap(budgetExceeded.limit_type, budgetExceeded.used)} /{' '}
                {formatCap(budgetExceeded.limit_type, budgetExceeded.limit)}
              </Figure>
              )
            </span>
          )}
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
