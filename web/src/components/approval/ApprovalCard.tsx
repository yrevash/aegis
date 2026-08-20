'use client'

import { Check, ShieldAlert, X } from 'lucide-react'
import { useId, type ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Button } from '@/components/primitives/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { signalForRisk } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { ApprovalDecision } from '@/lib/api/types'
import type { ApprovalRequired } from '@/lib/stream'

import { readApproval, type ApprovalView, type AuthorisedAction } from './approvalActions'

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
 * Three things about the *decision* are now stated rather than implied, because the
 * hard rule for a consequential control is that it says exactly what it will do:
 *
 * - **The consent sentence is always on screen**, not only on a fan-out. `Approving
 *   runs this one call.` is as much a statement of consequence as `…all three of these
 *   calls.`, and a card that only counts when the count is interesting teaches a reader
 *   that silence means one.
 * - **The button carries the count** and is described by that sentence, so the fact
 *   reaches a screen-reader user at the control rather than three paragraphs above it.
 * - **A gate that enumerated nothing says so.** `representativeOnly` means the wire
 *   carried no list and this is the one call the event named — a stated absence in the
 *   slot the list would occupy, rather than a list that looks complete.
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
  const consentId = useId()

  return (
    <Card
      className={cn(
        'rounded-lg border-risk/50 bg-risk/[0.04]',
        // An unresolved gate is emphasised by weight, not by a coloured glow: a
        // 28px risk-hued shadow is the one thing on the page that looks lit.
        !resolved && 'border-2 border-risk',
      )}
    >
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <ShieldAlert className="size-4 shrink-0 text-risk" aria-hidden />
        <CardTitle className="text-risk-ink">Approval required</CardTitle>
        <Badge variant={riskSignal === 'block' ? 'block' : 'risk'} className="ml-auto uppercase">
          {approval.risk} risk
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {view.actions.length > 0 && (
          <div>
            <p className="eyebrow mb-1.5">
              {view.many ? (
                <>
                  Proposed actions · <Figure>{view.actions.length}</Figure>
                </>
              ) : (
                'Proposed action'
              )}
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

        <ConsentStatement id={consentId} view={view} />

        <div className="flex flex-col gap-2 pt-1 sm:flex-row">
          <Button
            className="flex-1"
            aria-describedby={consentId}
            disabled={resolved}
            onClick={() => onDecision('approve')}
          >
            <Check className="size-4" aria-hidden />
            {view.many ? `Approve all ${view.actions.length}` : 'Approve'}
          </Button>
          <Button
            variant="outline"
            className="flex-1 border-block/60 text-block-ink hover:bg-block/10 hover:text-block-ink"
            aria-describedby={consentId}
            disabled={resolved}
            onClick={() => onDecision('reject')}
          >
            <X className="size-4" aria-hidden /> Reject
          </Button>
        </div>

        <GateReceipt approvalId={approval.approval_id} view={view} />
      </CardContent>
    </Card>
  )
}

/**
 * The one sentence naming what Approve does, always rendered.
 *
 * Exported for the durable inbox, which asks the identical question about the identical
 * gate — and a second spelling of the consent sentence is a second thing to keep true.
 */
export function ConsentStatement({
  id,
  view,
  className,
}: {
  id: string
  view: ApprovalView
  className?: string
}): ReactElement {
  return (
    <p
      id={id}
      className={cn('text-[0.8rem] leading-relaxed font-medium text-risk-ink', className)}
    >
      {view.summary} Rejecting ends the run without running any of them.
    </p>
  )
}

/**
 * Where the list came from — the gate id, and whether the backend enumerated it.
 *
 * A gate whose wire carried no `actions` list is showing the representative call, and
 * that is a genuinely different fact from a gate that enumerated exactly one. Saying
 * which is the receipt discipline applied to the one screen where being wrong about
 * "how many" is a wrong decision rather than an ugly page.
 */
export function GateReceipt({
  approvalId,
  view,
  className,
}: {
  approvalId: string
  view: ApprovalView
  className?: string
}): ReactElement {
  return (
    <Receipt
      label="Gate"
      origin={approvalId}
      detail={
        view.representativeOnly
          ? 'this gate enumerated no call list — the action shown is the one it named'
          : `${view.actions.length} ${view.actions.length === 1 ? 'call' : 'calls'} enumerated by the backend`
      }
      className={className}
    />
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
 *
 * Arguments are a two-column grid rather than a justified row: a `justify-between`
 * pair puts every value at a different left edge, so a reader checking four arguments
 * against what they expected has to re-find the column on every line.
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
    <li className="rounded-lg border border-border bg-surface-2 p-2.5">
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
        <dl className="mt-1.5 grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)] gap-x-3 gap-y-1 border-t border-border pt-1.5">
          {entries.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="font-mono text-[0.68rem] tracking-wide text-muted-foreground uppercase">
                {k}
              </dt>
              <dd className="tabular min-w-0 font-mono text-[0.72rem] break-all text-foreground">
                {String(v)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}
