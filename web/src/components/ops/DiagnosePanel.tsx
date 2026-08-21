'use client'

import { ArrowRight, FlaskConical, Loader2 } from 'lucide-react'
import { useMemo, useState, type ReactElement } from 'react'

import { BarChart } from '@/components/charts/BarChart'
import { SceneState } from '@/components/illustration/Scene'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { ErrorState } from '@/components/primitives/States'
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
    <Card className="flex min-w-0 flex-col">
      <CardHeader
        eyebrow="POST /ops/diagnose"
        title="Diagnosis"
        actions={
          <InfoTip label="About diagnosis">
            Finds the dominant failure mode in recent evals and drafts a fix.
          </InfoTip>
        }
      />
      <CardBody className="min-w-0 space-y-4">
        <button
          type="button"
          onClick={() => void runDiagnose()}
          disabled={busy !== null}
          className="inline-flex items-center gap-2 rounded-lg bg-foreground h-11 touch-manipulation px-4 text-[0.82rem] font-medium text-background transition-opacity hover:opacity-90 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-50"
        >
          {busy === 'diagnose' ? (
            <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
          ) : (
            <FlaskConical className="size-4" aria-hidden />
          )}
          {busy === 'diagnose' ? 'Diagnosing…' : 'Diagnose failures'}
        </button>

        {error && <ErrorState error={error} />}

        {!diag && !error && (
          /* A fault being diagnosed — before the button is pressed there is
             nothing under it but dead space. */
          <SceneState name="diagnose" size="sm">
            <p className="text-sm text-muted-foreground">
              Nothing diagnosed this session.
            </p>
          </SceneState>
        )}

        {diag && (
          <div className="min-w-0 space-y-3 rounded-lg border border-border bg-surface-2/40 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="eyebrow">Failing metrics</span>
              {diag.draft_version_id != null && (
                <Badge tone="graph" className="ml-auto">
                  draft #{diag.draft_version_id}
                </Badge>
              )}
            </div>
            <p className="text-xs leading-snug break-words text-foreground">
              {diag.failure_summary}
            </p>
            {breakdown.length > 0 && (
              <div className="min-w-0">
                <BarChart
                  allowDecimals={false}
                  data={breakdown}
                  index="metric"
                  category="count"
                  color="block"
                  height={160}
                  valueFormatter={(v) => `${v}`}
                />
              </div>
            )}
            <Receipt origin={`ops.diagnose · ${diag.failures_considered} failures reviewed`} />

            {!release && (
              <button
                type="button"
                onClick={() => void runRelease()}
                disabled={busy !== null || diag.draft_version_id == null}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border h-10 touch-manipulation px-3 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-50"
              >
                {busy === 'release' ? (
                  <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
                ) : null}
                {busy === 'release' ? 'Releasing…' : 'Release draft'}{' '}
                <ArrowRight className="size-3.5" aria-hidden />
              </button>
            )}
          </div>
        )}

        {release && (
          <div className="min-w-0 space-y-2.5 rounded-lg border border-border p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="eyebrow">Release outcome</span>
              <Badge tone={RISK_TONE[release.risk_level] ?? 'risk'}>
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
              <span className="flex items-baseline gap-1">
                <Figure className="font-semibold text-foreground">
                  {release.eval_score.toFixed(3)}
                </Figure>
                <span className="eyebrow">draft</span>
              </span>
              <ArrowRight className="size-3 text-muted-foreground" aria-hidden />
              <span className="flex items-baseline gap-1">
                <Figure className="text-muted-foreground">
                  {release.baseline_score.toFixed(3)}
                </Figure>
                <span className="eyebrow">baseline</span>
              </span>
              <span
                className={cn(
                  'tabular ml-auto rounded-md px-1.5 py-0.5 font-mono text-[0.72rem] font-medium',
                  release.eval_score >= release.baseline_score
                    ? 'bg-ok/20 text-[color:var(--ok-ink)]'
                    : 'bg-block/25 text-[color:var(--block-ink)]',
                )}
              >
                <span aria-hidden>{release.eval_score >= release.baseline_score ? '▲' : '▼'}</span>{' '}
                {Math.abs(release.eval_score - release.baseline_score).toFixed(3)}
              </span>
            </div>
            <p className="text-xs leading-snug break-words text-muted-foreground">
              {release.reason}
            </p>
            {release.risk_reasons.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {release.risk_reasons.map((r) => (
                  <Badge key={r} tone="risk">
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
