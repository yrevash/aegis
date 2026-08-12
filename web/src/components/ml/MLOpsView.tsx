'use client'

import { Boxes, Loader2, RefreshCw, Sigma, Target, WifiOff } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { ConformalBand } from '@/components/charts/ConformalBand'
import { ShapWaterfall } from '@/components/charts/ShapWaterfall'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { Gauge } from '@/components/primitives/Gauge'
import { getModelCard, mlExplain } from '@/lib/api/client'
import type { MLExplainRequest, MLExplainResponse } from '@/lib/api/types'
import type { ModelCardResponse } from '@/lib/api/platform'
import { probeBackend, type ResolvedMode } from '@/lib/api/mode'

/**
 * An example prediction to explain. Against a live backend these feature values
 * drive the score; the mock returns a fixed explanation, so the panel is a
 * labelled sample the moment there is no real backend behind it.
 */
const EXAMPLE: MLExplainRequest = {
  features: {
    duplicate_charge_confirmed: 1,
    account_tenure_months: 41,
    premium_tier: 1,
    prior_refunds_90d: 2,
    amount_usd: 4200,
    chargeback_risk: 0.22,
  },
}

/** How the training frame was sourced → an honest tone + label for the badge. */
function dataSourceBadge(source: string): { tone: BadgeTone; label: string } {
  switch (source) {
    case 'provided':
      return { tone: 'ok', label: 'provided data' }
    case 'spec_provider':
      return { tone: 'agent', label: 'spec provider' }
    case 'synthetic':
      return { tone: 'risk', label: 'synthetic (demo)' }
    default:
      return { tone: 'neutral', label: source }
  }
}

/** One fact on the model card — a mono label over a value with an optional detail. */
function Fact({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail?: string
}): ReactElement {
  return (
    <div className="flex flex-col gap-1 rounded-xl border border-border bg-surface-2/40 p-3.5">
      <span className="eyebrow">{label}</span>
      <span className="t-title tabular text-[0.95rem] font-semibold text-foreground">{value}</span>
      {detail ? (
        <span className="text-[0.72rem] leading-snug text-muted-foreground">{detail}</span>
      ) : null}
    </div>
  )
}

/**
 * MLOps — the `aegis.ml` surface. A model card states, from measured metadata,
 * what the ensemble is and the guarantee it ships (soft-voting gradient-boosted
 * members, conformal calibration, a coverage target), and an interactive
 * "explain a prediction" panel renders the two things that make a score
 * trustworthy: the calibrated Gauge + ConformalBand and the full ShapWaterfall
 * of signed drivers. Every figure sits in an aligned tabular-mono column, and
 * numbers/labels come straight from the accessors — nothing is fabricated.
 */
function MLOpsView(): ReactElement {
  const token: string | null = null

  // ── Model card ─────────────────────────────────────────────────────────────
  const [card, setCard] = useState<ModelCardResponse | null>(null)
  const [cardError, setCardError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    getModelCard(token)
      .then((c) => {
        if (alive) setCard(c)
      })
      .catch(() => {
        if (alive) setCardError('Could not load the model card. Is the backend running?')
      })
    return () => {
      alive = false
    }
  }, [token])

  // ── Explain a prediction ─────────────────────────────────────────────────────
  const [result, setResult] = useState<MLExplainResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const explain = useCallback(() => {
    setLoading(true)
    setError(null)
    mlExplain(EXAMPLE, token)
      .then(setResult)
      .catch(() => setError('Could not reach the model. Is the backend running?'))
      .finally(() => setLoading(false))
  }, [token])

  useEffect(explain, [explain])

  // SHAP is additive: base = prediction − Σ contribution (numeric targets only).
  const base = useMemo(() => {
    if (result == null || typeof result.prediction !== 'number') return 0
    return result.prediction - result.shap_attribution.reduce((s, f) => s + f.contribution, 0)
  }, [result])

  const numeric = result != null && typeof result.prediction === 'number'
  const source = card != null ? dataSourceBadge(card.data_source) : null

  return (
    <div className="space-y-6">
      {/* Section header */}
      <div>
        <p className="eyebrow mb-1">SHAP · conformal · XGBoost + MAPIE</p>
        <h1 className="t-hero text-foreground">MLOps</h1>
      </div>

      {/* ── Model card ─────────────────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          eyebrow="aegis.ml"
          title="Model card"
          description="Measured metadata for the fitted ensemble — no fabricated figures."
          actions={
            source ? (
              <Badge tone={source.tone} className="gap-1.5">
                <Sigma className="size-3" />
                {source.label}
              </Badge>
            ) : null
          }
        />
        <CardBody className="space-y-5">
          {cardError ? (
            <p className="py-8 text-center text-sm text-danger">{cardError}</p>
          ) : card == null ? (
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading model card…
            </div>
          ) : (
            <>
              {/* Headline stats */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <StatCard
                  label="Task"
                  value={card.task}
                  icon={Target}
                  tone="ml"
                />
                <StatCard
                  label="Features"
                  value={`${card.n_features} → ${card.encoded_feature_count}`}
                  icon={Boxes}
                  tone="graph"
                />
                <StatCard
                  label="Coverage target"
                  value={`${Math.round(card.conformal_coverage * 100)}%`}
                  icon={Sigma}
                  tone="agent"
                />
                <StatCard
                  label="Train / calib"
                  value={`${card.training_size} / ${card.calibration_size}`}
                  icon={Boxes}
                  tone="neutral"
                />
              </div>

              {/* Facts panel */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                <Fact
                  label="target"
                  value={card.target}
                  detail={`${card.task} target predicted by the ensemble`}
                />
                <Fact
                  label="conformal method"
                  value={card.conformal_method}
                  detail={`${card.conformal_predictor} · distribution-free coverage guarantee`}
                />
                <Fact
                  label="data source"
                  value={source?.label ?? card.data_source}
                  detail={
                    card.data_source === 'synthetic'
                      ? 'Offline there is no domain-trained frame — labelled synthetic, honestly.'
                      : 'How the training frame was sourced.'
                  }
                />
                <Fact
                  label="categorical features"
                  value={String(card.categorical_features.length)}
                  detail={card.categorical_features.join(', ') || '—'}
                />
                <Fact
                  label="numeric features"
                  value={String(card.numeric_features.length)}
                  detail={card.numeric_features.join(', ') || '—'}
                />
                <Fact
                  label="calibration size"
                  value={card.calibration_size.toLocaleString()}
                  detail="Held-out rows the conformal interval is calibrated on."
                />
              </div>

              {/* Ensemble members */}
              <div>
                <p className="eyebrow mb-2">ensemble members · soft-voting</p>
                <div className="overflow-x-auto rounded-xl border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground">
                        <th className="px-4 py-2 font-medium">member</th>
                        <th className="px-4 py-2 font-medium">kind</th>
                        <th className="px-4 py-2 text-right font-medium">weight</th>
                      </tr>
                    </thead>
                    <tbody>
                      {card.ensemble_members.map((m) => (
                        <tr key={m.name} className="border-b border-border last:border-0">
                          <td className="px-4 py-2 font-mono text-foreground">{m.name}</td>
                          <td className="px-4 py-2 text-muted-foreground">{m.kind}</td>
                          <td className="tabular px-4 py-2 text-right font-mono text-foreground">
                            {m.weight.toFixed(2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </CardBody>
      </Card>

      {/* ── Explain a prediction ───────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          eyebrow="aegis.ml"
          title="Explain a prediction"
          description="A sample feature set, scored and explained — gauge + calibrated band on one side, the full SHAP driver walk on the other."
          actions={
            <button
              type="button"
              onClick={explain}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            >
              {loading ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <RefreshCw className="size-3.5" />
              )}
              Explain
            </button>
          }
        />
        <CardBody className="space-y-4">
          {/* The input features being explained */}
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(EXAMPLE.features).map(([k, v]) => (
              <span
                key={k}
                className="tabular inline-flex items-center gap-1 rounded-md bg-surface-2 px-2 py-0.5 font-mono text-[0.68rem] text-muted-foreground"
              >
                <span className="text-foreground">{k}</span>
                <span>=</span>
                <span className="font-semibold text-foreground">{String(v)}</span>
              </span>
            ))}
          </div>

          {error ? (
            <p className="py-8 text-center text-sm text-danger">{error}</p>
          ) : result == null ? (
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Scoring…
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {/* Confidence — gauge + calibrated band */}
              <div className="flex flex-col gap-4 rounded-xl border border-border bg-surface-2/40 p-4">
                {result.conformal_confidence != null && (
                  <div className="flex justify-center">
                    <Gauge
                      value={result.conformal_confidence}
                      color="ml"
                      size={120}
                      label="calibrated"
                    />
                  </div>
                )}
                <ConformalBand
                  prediction={result.prediction}
                  interval={result.conformal_interval}
                  confidence={result.conformal_confidence}
                  intervalWidth={result.interval_width}
                  setSize={result.prediction_set_size}
                />
              </div>

              {/* Why — full SHAP waterfall */}
              <div className="rounded-xl border border-border bg-surface-2/40 p-4">
                {numeric && typeof result.prediction === 'number' ? (
                  <ShapWaterfall
                    base={base}
                    features={result.shap_attribution}
                    prediction={result.prediction}
                  />
                ) : (
                  <p className="text-sm text-muted-foreground">
                    A driver breakdown appears for numeric targets.
                  </p>
                )}
              </div>
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  )
}

/**
 * Client entry for the MLOps section. Runs the boot probe once (live-first, mock
 * fallback) before mounting the view, so the model-card + explain fetches read
 * the resolved mode — the offline demo seeds from the mock fixtures and is
 * labelled with the honest banner.
 */
export function MLOpsMount(): ReactElement {
  const [mode, setMode] = useState<ResolvedMode | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setMode(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (mode === null) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <div>
      {mode.mode === 'mock' && (
        <div
          role="status"
          className="mb-4 flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
        >
          <WifiOff className="size-3.5 shrink-0" />
          <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
        </div>
      )}
      <MLOpsView />
    </div>
  )
}
