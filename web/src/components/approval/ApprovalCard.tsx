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

import { readApproval, type AuthorisedAction } from './approvalActions'

interface ApprovalCardProps {
  approval: ApprovalRequired
  onDecision: (decision: ApprovalDecision) => void
  /** Set true once a decision was submitted, to disable the controls. */
  resolved?: boolean
}

/**
 * The human-in-the-loop gate. When the agent proposes a high-risk action it pauses here;
 * the reviewer sees **every call the approval authorises**, each with its arguments and
 * its own risk, then approves or rejects. Central to the bounded-autonomy story — nothing
 * high-risk executes without this decision.
 *
 * The list is the point. `approval.action` is only the representative — the highest-risk
 * call — and a fan-out can have three sub-agents each propose a consequential write in
 * one turn. A card that renders `action` alone asks the person to authorise three writes
 * while naming one, which is the Phase 5 defect at the only layer where a human reads it.
 * {@link readApproval} decides what will run; this renders it and counts it out loud.
 *
 * Most runs propose one call, and that case keeps the shape it always had: one action,
 * its arguments, no count and no per-call badge, because the header already carries the
 * risk. The ceremony appears only when there is more than one thing to consent to.
 *
 * Every field rendered here arrives on the live `approval_required` stream event the
 * backend emits from the graph interrupt, and the decision goes back out on
 * `POST /approval`, which resumes the parked run.
 */
export function ApprovalCard({
  approval,
  onDecision,
  resolved,
}: ApprovalCardProps): ReactElement {
  const riskSignal = signalForRisk(approval.risk)
  const view = readApproval(approval)

  return (
    <Card
      className={cn(
        'border-risk/50 bg-risk/[0.04]',
        // An unresolved gate is emphasised by weight, not by a coloured glow: a
        // 28px risk-hued shadow is the one thing on the page that looks lit.
        !resolved && 'border-2 border-risk',
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
        {view.actions.length > 0 && (
          <div>
            <p className="eyebrow mb-1.5">
              {view.many ? `Proposed actions · ${view.actions.length}` : 'Proposed action'}
            </p>
            <ul className="grid gap-2">
              {view.actions.map((action, index) => (
                <ProposedAction
                  key={action.id === '' ? `${action.name}-${index}` : action.id}
                  action={action}
                  showRisk={view.many}
                />
              ))}
            </ul>
          </div>
        )}

        <div>
          <p className="eyebrow mb-1">Why this needs approval</p>
          <p className="text-[0.8rem] leading-relaxed text-muted-foreground">{approval.rationale}</p>
        </div>

        {/* Only on a multi-call gate. On the ordinary one-call run the list is one box
            and the button beside it says Approve — a sentence counting it to one is the
            ceremony this card was asked not to grow. */}
        {view.many && (
          <p className="text-[0.8rem] leading-relaxed font-medium text-risk-ink">{view.summary}</p>
        )}

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

/**
 * One call the approval authorises: what runs, with what, and at what risk.
 *
 * Exported because the durable approvals inbox (§7.1) renders the same list. A gate
 * that authorises three calls must show three wherever it is read, and a second
 * renderer is a second chance to show two.
 *
 * The per-call risk chip appears only on a multi-call gate. On the common single-call
 * run the header badge already says the risk, and repeating it two lines down is noise
 * on the one card that must be read in a hurry.
 */
export function ProposedAction({
  action,
  showRisk,
}: {
  action: AuthorisedAction
  showRisk: boolean
}): ReactElement {
  const entries = Object.entries(action.args)
  const signal = signalForRisk(action.risk)

  return (
    <li className="rounded-md border border-border bg-surface-2 p-2.5">
      <div className="flex items-baseline gap-2">
        <p className="min-w-0 flex-1 font-mono text-[0.8rem] font-medium break-words text-foreground">
          {action.name}
        </p>
        {showRisk && (
          <Badge
            variant={signal === 'block' ? 'block' : signal === 'risk' ? 'risk' : 'ok'}
            className="shrink-0 uppercase"
          >
            {action.risk}
          </Badge>
        )}
      </div>

      {entries.length > 0 && (
        <dl className="mt-1.5 grid gap-1 border-t border-border pt-1.5">
          {entries.map(([k, v]) => (
            <div key={k} className="flex items-baseline justify-between gap-3">
              <dt className="font-mono text-[0.68rem] tracking-wide text-muted-foreground uppercase">
                {k}
              </dt>
              <dd className="tabular font-mono text-[0.72rem] break-all text-foreground">
                {String(v)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}
