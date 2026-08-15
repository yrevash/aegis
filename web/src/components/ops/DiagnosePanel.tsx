'use client'

import { ArrowRight, FlaskConical, Loader2 } from 'lucide-react'
import { useMemo, useState, type ReactElement } from 'react'

import { BarChart } from '@/components/charts/BarChart'
import { InfoTip } from '@/components/primitives/InfoTip'
import { CountUp } from '@/components/shared'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { postOpsDiagnose, postOpsRelease } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { OpsDiagnoseResponse, OpsReleaseResponse } from '@/lib/api/ops'

import { PROMPT_KEY, RISK_TONE } from './opsShared'

const OUTCOME_LABEL: Record<string, { tone: 'ok' | 'risk' | 'block'; label: string }> = {
  promoted: { tone: 'ok', label: 'auto-shipped' },
  staged_for_approval: { tone: 'risk', label: 'waiting for approval' },
  rejected: { tone: 'block', label: 'rejected' },
}

/**
 * The **Diagnosis** panel — the head of the loop. It reads recent quality
 * failures (`POST /ops/diagnose`), shows which metric families are failing as a
 * bar chart, and drafts a better prompt; releasing that draft runs it through
 * the tiered gate (`POST /ops/release`) and surfaces the resulting risk tier +
 * eval delta.
 */
export function DiagnosePanel({ onChanged }: { onChanged: () => void }): ReactElement {
  // Both actions below are RBAC-scoped writes — send the real session bearer.
  const { session } = useAuth()
  const token = session?.token ?? null
  const [diag, setDiag] = useState<OpsDiagnoseResponse | null>(null)
  const [release, setRelease] = useState<OpsReleaseResponse | null>(null)
  const [busy, setBusy] = useState<null | 'diagnose' | 'release'>(null)
  const [error, setError] = useState<string | null>(null)

  const runDiagnose = async (): Promise<void> => {
    setBusy('diagnose')
    setError(null)
    setRelease(null)
    try {
      setDiag(await postOpsDiagnose(token, { prompt_key: PROMPT_KEY, limit: 50 }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Diagnose failed')
    } finally {
      setBusy(null)
    }
  }

  const runRelease = async (): Promise<void> => {
    if (diag?.draft_version_id == null) return
    setBusy('release')
    setError(null)
    try {
      const res = await postOpsRelease(token, {
        draft_version_id: diag.draft_version_id,
        autonomy: 'tiered',
      })
      setRelease(res)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Release failed')
    } finally {
      setBusy(null)
    }
  }

  const breakdown = useMemo(
    () =>
      diag
        ? Object.entries(diag.metric_breakdown)
            .map(([metric, count]) => ({ metric, count }))
            .sort((a, b) => b.count - a.count)
        : [],
    [diag],
  )

  return (
    <Card>
      <CardHeader
        eyebrow="POST /ops/diagnose"
        title="Diagnosis"
        actions={
          <InfoTip label="About diagnosis">
            Finds the dominant failure mode in recent evals and drafts a fix; releasing that draft
            sends it through the tiered gate.
          </InfoTip>
        }
      />
      <CardBody className="space-y-4">
        <button
          type="button"
          onClick={() => void runDiagnose()}
          disabled={busy !== null}
          className="inline-flex items-center gap-2 rounded-lg bg-foreground px-3.5 py-2 text-[0.82rem] font-medium text-background transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          {busy === 'diagnose' ? <Loader2 className="size-4 animate-spin" /> : <FlaskConical className="size-4" />}
          {busy === 'diagnose' ? 'Diagnosing…' : 'Diagnose failures'}
        </button>

        {error && <p className="text-xs text-danger">{error}</p>}

        {diag && (
          <div className="space-y-3 rounded-xl border border-border bg-surface-2/40 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="eyebrow">Failing metrics</span>
              <span className="font-mono text-[0.62rem] text-muted-foreground">
                {diag.failures_considered} failures reviewed
              </span>
              {diag.draft_version_id != null && (
                <Badge tone="graph" className="ml-auto text-[0.56rem]">
                  draft #{diag.draft_version_id}
                </Badge>
              )}
            </div>
            <p className="text-xs leading-snug text-foreground">{diag.failure_summary}</p>
            {breakdown.length > 0 && (
              <BarChart
                data={breakdown}
                index="metric"
                category="count"
                color="block"
                height={160}
                valueFormatter={(v) => `${v}`}
              />
            )}

            {!release && (
              <button
                type="button"
                onClick={() => void runRelease()}
                disabled={busy !== null || diag.draft_version_id == null}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              >
                {busy === 'release' ? 'Releasing…' : 'Release draft'} <ArrowRight className="size-3.5" />
              </button>
            )}
          </div>
        )}

        {release && (
          <div className="space-y-2.5 rounded-xl border border-border p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="eyebrow">Release outcome</span>
              <Badge tone={RISK_TONE[release.risk_level] ?? 'risk'} className="text-[0.56rem]">
                {release.risk_level} risk
              </Badge>
              <Badge
                tone={(OUTCOME_LABEL[release.outcome] ?? { tone: 'risk' as const }).tone}
                className="ml-auto"
              >
                {(OUTCOME_LABEL[release.outcome] ?? { label: release.outcome }).label}
              </Badge>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-baseline gap-1">
                <CountUp
                  value={release.eval_score}
                  format={(n) => n.toFixed(3)}
                  className="t-title font-semibold text-ok-ink"
                />
                <span className="font-mono text-[0.6rem] text-muted-foreground">draft</span>
              </div>
              <ArrowRight className="size-3 text-muted-foreground" />
              <div className="flex items-baseline gap-1">
                <span className="tabular font-display text-lg font-semibold text-muted-foreground">
                  {release.baseline_score.toFixed(3)}
                </span>
                <span className="font-mono text-[0.6rem] text-muted-foreground">baseline</span>
              </div>
              <span
                className={cn(
                  'tabular ml-auto rounded-md px-1.5 py-0.5 font-mono text-[0.62rem] font-medium',
                  release.eval_score >= release.baseline_score
                    ? 'bg-ok/15 text-ok-ink'
                    : 'bg-block/15 text-block-ink',
                )}
              >
                {release.eval_score >= release.baseline_score ? '▲' : '▼'}{' '}
                {Math.abs(release.eval_score - release.baseline_score).toFixed(3)}
              </span>
            </div>
            <p className="text-xs leading-snug text-muted-foreground">{release.reason}</p>
            {release.risk_reasons.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {release.risk_reasons.map((r) => (
                  <Badge key={r} tone="risk" className="text-[0.56rem]">
                    {r}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  )
}
