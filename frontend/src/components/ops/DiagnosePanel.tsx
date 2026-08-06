import { ArrowRight, FlaskConical, Stethoscope } from 'lucide-react'
import { useMemo, useState, type ReactElement } from 'react'

import { postOpsDiagnose, postOpsRelease } from '@/api/client'
import { BarChart } from '@/components/charts/BarChart'
import { CountUp } from '@/components/shared'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { OpsDiagnoseResponse, OpsReleaseResponse } from '@/types/ops'

import { PanelHead } from './PanelHead'

interface Props {
  token: string | null
  promptKey: string
  onChanged: () => void
}

const OUTCOME_STYLE: Record<string, { variant: 'ok' | 'risk' | 'block'; label: string }> = {
  promoted: { variant: 'ok', label: 'auto-shipped' },
  staged_for_approval: { variant: 'risk', label: 'waiting for approval' },
  rejected: { variant: 'block', label: 'rejected' },
}

/**
 * The **Diagnosis** panel (§4.4) — the head of the loop. It reads the recent
 * quality failures, shows which metric families are failing as a bar chart, and
 * drafts a better prompt; releasing that draft runs it through the release gate.
 * Card-less: a `BentoTile` owns the surrounding Card.
 */
export function DiagnosePanel({ token, promptKey, onChanged }: Props): ReactElement {
  const [diag, setDiag] = useState<OpsDiagnoseResponse | null>(null)
  const [release, setRelease] = useState<OpsReleaseResponse | null>(null)
  const [busy, setBusy] = useState<null | 'diagnose' | 'release'>(null)
  const [error, setError] = useState<string | null>(null)

  const runDiagnose = async (): Promise<void> => {
    setBusy('diagnose')
    setError(null)
    setRelease(null)
    try {
      setDiag(await postOpsDiagnose(token, { prompt_key: promptKey, limit: 50 }))
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
      const res = await postOpsRelease(token, { draft_version_id: diag.draft_version_id, autonomy: 'tiered' })
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
    <>
      <PanelHead
        icon={Stethoscope}
        tint="bg-block/12"
        ink="text-block-ink"
        title="Diagnosis"
        subtitle="Find the failure mode · draft a fix"
        info="Reads recent quality failures (POST /ops/diagnose), finds the dominant failure mode, and drafts a better prompt. Releasing sends the draft through the gate."
      />

      <div className="space-y-3.5">
        <Button onClick={() => void runDiagnose()} disabled={busy !== null} className="w-full sm:w-auto">
          <FlaskConical /> {busy === 'diagnose' ? 'Diagnosing…' : 'Diagnose failures'}
        </Button>

        {error && <p className="text-xs text-destructive">{error}</p>}

        {diag && (
          <div className="space-y-3 rounded-lg border border-border bg-surface-2/40 p-3.5">
            <div className="flex items-center gap-2">
              <span className="eyebrow">Failing metrics</span>
              <span className="font-mono text-[0.62rem] text-muted-foreground">
                {diag.failures_considered} failures reviewed
              </span>
              {diag.draft_version_id != null && (
                <Badge variant="graph" className="ml-auto text-[0.56rem]">
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
              <Button
                size="sm"
                variant="outline"
                onClick={() => void runRelease()}
                disabled={busy !== null || diag.draft_version_id == null}
              >
                {busy === 'release' ? 'Releasing…' : 'Release draft'} <ArrowRight />
              </Button>
            )}
          </div>
        )}

        {release && (
          <div className="space-y-2 rounded-lg border border-border p-3.5">
            <div className="flex items-center gap-2">
              <span className="eyebrow">Release outcome</span>
              <Badge
                variant={(OUTCOME_STYLE[release.outcome] ?? OUTCOME_STYLE.staged_for_approval).variant}
                className="ml-auto"
              >
                {(OUTCOME_STYLE[release.outcome] ?? { label: release.outcome }).label}
              </Badge>
            </div>
            <div className="flex items-center gap-3">
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
                  'tabular ml-auto rounded-sm px-1.5 py-0.5 font-mono text-[0.62rem] font-medium',
                  release.eval_score >= release.baseline_score ? 'bg-ok/10 text-ok-ink' : 'bg-block/10 text-block-ink',
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
                  <Badge key={r} variant="risk" className="text-[0.56rem]">
                    {r}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </>
  )
}
