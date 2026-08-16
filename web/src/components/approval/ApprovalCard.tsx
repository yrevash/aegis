'use client'

import { Check, ShieldAlert, X } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Button } from '@/components/primitives/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { signalForRisk } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { ApprovalDecision } from '@/lib/api/types'
import type { ApprovalRequired } from '@/lib/stream'

interface ApprovalCardProps {
  approval: ApprovalRequired
  onDecision: (decision: ApprovalDecision) => void
  /** Set true once a decision was submitted, to disable the controls. */
  resolved?: boolean
}

/**
 * The human-in-the-loop gate. When the agent proposes a high-risk action it
 * pauses here; the reviewer sees the proposed action, its arguments, the risk
 * level and the rationale, then approves or rejects. Central to the bounded-
 * autonomy story — nothing high-risk executes without this decision.
 *
 * Every field rendered here arrives on the live `approval_required` stream event
 * the backend emits from the graph interrupt, and the decision goes back out on
 * `POST /approval`, which resumes the parked run.
 */
export function ApprovalCard({
  approval,
  onDecision,
  resolved,
}: ApprovalCardProps): ReactElement {
  const riskSignal = signalForRisk(approval.risk)

  return (
    <Card
      className={cn(
        'border-risk/50 bg-risk/[0.04]',
        !resolved && 'shadow-[0_0_28px_-8px_var(--risk)]',
      )}
    >
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <ShieldAlert className="size-4 text-risk" />
        <CardTitle className="text-risk-ink">Approval required</CardTitle>
        <Badge variant={riskSignal === 'block' ? 'block' : 'risk'} className="ml-auto uppercase">
          {approval.risk} risk
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <p className="eyebrow mb-1">Proposed action</p>
          <p className="text-sm font-medium text-foreground">{approval.action}</p>
        </div>

        {Object.keys(approval.args).length > 0 && (
          <div className="rounded-md border border-border bg-surface-2 p-2.5">
            <dl className="grid gap-1">
              {Object.entries(approval.args).map(([k, v]) => (
                <div key={k} className="flex items-baseline justify-between gap-3">
                  <dt className="font-mono text-[0.68rem] tracking-wide text-muted-foreground uppercase">
                    {k}
                  </dt>
                  <dd className="tabular font-mono text-[0.72rem] text-foreground">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}

        <div>
          <p className="eyebrow mb-1">Why this needs approval</p>
          <p className="text-[0.8rem] leading-relaxed text-muted-foreground">{approval.rationale}</p>
        </div>

        <div className="flex gap-2 pt-1">
          <Button
            className="flex-1 bg-ok text-ok-foreground hover:bg-ok/90"
            disabled={resolved}
            onClick={() => onDecision('approve')}
          >
            <Check className="size-4" /> Approve
          </Button>
          <Button
            variant="outline"
            className="flex-1 border-block/60 text-block-ink hover:bg-block/10 hover:text-block-ink"
            disabled={resolved}
            onClick={() => onDecision('reject')}
          >
            <X className="size-4" /> Reject
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
