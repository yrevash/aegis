'use client'

import { Check, ShieldAlert, X } from 'lucide-react'
import { useMemo, type ReactElement } from 'react'

import { ConformalInterval } from '@/components/ml/ConformalInterval'
import { Badge } from '@/components/primitives/badge'
import { Button } from '@/components/primitives/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { signalForRisk } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { ApprovalDecision } from '@/lib/api/types'
import type { ApprovalRequired, MLExplanation } from '@/lib/stream'

interface ApprovalCardProps {
  approval: ApprovalRequired
  onDecision: (decision: ApprovalDecision) => void
  /** Set true once a decision was submitted, to disable the controls. */
  resolved?: boolean
  /**
   * The ML explanation behind the pause, if any. When present with a numeric
   * prediction and interval, the card embeds a mini conformal band and its
   * strongest driver so the reviewer sees *why* the model was uncertain — the
   * pause explains itself ("this wide band is why I stopped").
   */
  ml?: MLExplanation | null
  /** Optional unit for the embedded ML prediction, e.g. `'h'`. */
  unit?: string
}

/**
 * The human-in-the-loop gate. When the agent proposes a high-risk action it
 * pauses here; the reviewer sees the proposed action, its arguments, the risk
 * level and the rationale, then approves or rejects. Central to the bounded-
 * autonomy story — nothing high-risk executes without this decision.
 */
export function ApprovalCard({
  approval,
  onDecision,
  resolved,
  ml,
  unit,
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

        {ml != null && <ApprovalMlWhy ml={ml} unit={unit} />}

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
 * The self-explaining ML footnote inside the pause: a mini conformal band plus
 * the strongest driver. Renders nothing unless the prediction is numeric and an
 * interval is present (the only case where a band is meaningful).
 */
function ApprovalMlWhy({ ml, unit }: { ml: MLExplanation; unit?: string }): ReactElement | null {
  const topDriver = useMemo(() => {
    if (ml.shap_attribution.length === 0) return null
    return [...ml.shap_attribution].sort(
      (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution),
    )[0]
  }, [ml])

  if (typeof ml.prediction !== 'number' || ml.conformal_interval === null) return null

  return (
    <div className="rounded-md border border-ml/40 bg-ml/[0.05] p-2.5">
      <div className="mb-1.5 flex items-center gap-1.5">
        <p className="eyebrow">Confidence</p>
        <InfoTip label="About confidence">
          A calibrated confidence interval (conformal). A wide band means the model is uncertain,
          which is why the action was routed to a human.
        </InfoTip>
      </div>
      <ConformalInterval
        prediction={ml.prediction}
        interval={ml.conformal_interval}
        confidence={ml.conformal_confidence}
        unit={unit}
        width={ml.interval_width}
        setSize={ml.prediction_set_size}
        compact
      />
      {topDriver && (
        <p className="mt-2 text-[0.72rem] leading-snug text-muted-foreground">
          Biggest factor:{' '}
          <span className="font-mono text-foreground">{topDriver.feature}</span>.
        </p>
      )}
    </div>
  )
}