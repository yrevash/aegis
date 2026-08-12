'use client'

import { CheckCircle2, ChevronDown, Clock, Loader2, XCircle } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Button } from '@/components/primitives/button'
import { Card } from '@/components/primitives/card'
import { Gauge } from '@/components/primitives/Gauge'
import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'
import type { ApprovalDecision, ApprovalRow } from '@/lib/api/types'
import type { RiskLevel } from '@/lib/stream'

import { slaCountdown, type SlaUrgency } from './sla'

/** Map a risk level to a badge variant (low healthy, high blocks). */
function riskVariant(risk: RiskLevel): 'ok' | 'risk' | 'block' {
  return risk === 'high' ? 'block' : risk === 'medium' ? 'risk' : 'ok'
}

/** Countdown text colour per urgency. */
function slaClass(urgency: SlaUrgency): string {
  return urgency === 'overdue'
    ? 'text-block-ink'
    : urgency === 'warn'
      ? 'text-risk-ink'
      : 'text-muted-foreground'
}

interface ApprovalQueueCardProps {
  row: ApprovalRow
  now: number
  busy: boolean
  /** True while the card is optimistically leaving after a decision. */
  leaving: boolean
  onDecide: (row: ApprovalRow, decision: ApprovalDecision) => void
}

/**
 * One tile in the approvals triage grid: the proposed action, its risk, a live
 * SLA countdown, and a confidence gauge read in a glance — with the ML internals
 * tucked behind a "Why this needs approval" disclosure. Approve / reject act per
 * card; on a decision it fades out optimistically.
 */
export function ApprovalQueueCard({
  row,
  now,
  busy,
  leaving,
  onDecide,
}: ApprovalQueueCardProps): ReactElement {
  const [open, setOpen] = useState(false)
  const sla = slaCountdown(row.sla_deadline, now)
  const snap = row.ml_snapshot
  const confidence = snap?.conformal_confidence ?? null

  return (
    <Card
      className={cn(
        'flex flex-col gap-3 p-4 transition-[opacity,transform] duration-300 ease-out',
        leaving ? 'pointer-events-none translate-y-1 opacity-0' : 'opacity-100',
      )}
    >
      <div className="flex items-center gap-2">
        <Badge variant={riskVariant(row.risk)} className="uppercase">
          {row.risk} risk
        </Badge>
        <span
          className={cn(
            'ml-auto inline-flex items-center gap-1 font-mono text-[0.7rem]',
            slaClass(sla.urgency),
          )}
        >
          <Clock className="size-3" />
          {sla.text}
        </span>
      </div>

      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">{row.action}</p>
          {row.persona && (
            <p className="mt-0.5 font-mono text-[0.66rem] text-muted-foreground">{row.persona}</p>
          )}
        </div>
        {confidence != null && (
          <div className="shrink-0">
            <Gauge value={confidence} size={72} color="ml" label="Confidence" />
          </div>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button size="sm" className="flex-1" onClick={() => onDecide(row, 'approve')} disabled={busy}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />}
          Approve
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="flex-1"
          onClick={() => onDecide(row, 'reject')}
          disabled={busy}
        >
          <XCircle className="size-4" />
          Reject
        </Button>
      </div>

      {/* The reasoning + ML internals live one layer down. */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-1 text-left text-[0.72rem] font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronDown className={cn('size-3.5 transition-transform', open && 'rotate-180')} />
        Why this needs approval
      </button>

      {open && (
        <div className="space-y-2 rounded-md border border-border/70 bg-surface-2/40 p-3">
          <p className="text-[0.76rem] leading-snug text-muted-foreground">{row.rationale}</p>
          {snap && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[0.66rem] text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                Model read
                <InfoTip label="About the model read">
                  A calibrated confidence interval (conformal) plus the model&apos;s point prediction —
                  the uncertainty that routed this action to a human.
                </InfoTip>
              </span>
              {snap.prediction != null && <span>pred {String(snap.prediction)}</span>}
              {snap.conformal_interval && (
                <span>
                  interval [{snap.conformal_interval[0]}, {snap.conformal_interval[1]}]
                </span>
              )}
              {snap.conformal_confidence != null && (
                <span>{Math.round(snap.conformal_confidence * 100)}% coverage</span>
              )}
              {snap.band && <span className="text-ml-ink">band {snap.band}</span>}
            </div>
          )}
          <p className="font-mono text-[0.64rem] text-muted-foreground/80">run {row.run_id}</p>
        </div>
      )}
    </Card>
  )
}
