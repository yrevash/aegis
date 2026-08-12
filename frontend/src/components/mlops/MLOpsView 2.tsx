import { Boxes, Loader2, RefreshCw, Sigma, Target } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { mlExplain } from '@/api/client'
import { ConformalBand } from '@/components/charts/ConformalBand'
import { ShapWaterfall } from '@/components/charts/ShapWaterfall'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Gauge } from '@/components/ui/Gauge'
import type { MLExplainRequest, MLExplainResponse } from '@/types/api'

/**
 * An example prediction to explain. Against a live backend these feature values
 * drive the score; the mock returns a fixed explanation, so the panel is
 * labelled as a sample the moment there is no real backend.
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

/** One fact on the model card — a label over a value with an optional detail. */
function Fact({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: typeof Boxes
  label: string
  value: string
  detail: string
}): ReactElement {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface p-3">
      <span className="eyebrow flex items-center gap-1.5">
        <Icon className="size-3.5 text-muted-foreground" />
        {label}
      </span>
      <span className="font-display text-[0.95rem] font-semibold text-foreground">{value}</span>
      <span className="text-[0.72rem] leading-snug text-muted-foreground">{detail}</span>
    </div>
  )
}

/**
 * MLOps — the `aegis.ml` surface. A model card states what the ensemble is and
 * the guarantee it ships (gradient-boosted score, conformal calibration via
 * MAPIE, SHAP attributions), and an interactive "explain a prediction" panel
 * renders the two things that make a score trustworthy: the calibrated
 * ConformalBand and the full ShapWaterfall of drivers. Every figure sits in an
 * aligned tabular-mono column.
 */
export function MLOpsView({ token }: { token: string | null }): ReactElement {
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

  return (
    <div className="flex flex-col gap-4">
      {/* Model card */}
      <Card>
        <CardHeader className="flex-row items-center gap-2 space-y-0">
          <CardTitle>Model card</CardTitle>
          <span className="eyebrow ml-auto">aegis.ml</span>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Fact
              icon={Boxes}
              label="ensemble"
              value="Gradient-boosted trees"
              detail="A tabular scorer (XGBoost-class) over the decision's features."
            />
            <Fact
              icon={Sigma}
              label="calibration"
              value="Conformal · MAPIE"
              detail="Split-conformal intervals with a distribution-free coverage guarantee."
            />
            <Fact
              icon={Target}
              label="explainability"
              value="SHAP attribution"
              detail="Signed per-feature contributions that add up from base to prediction."
            />
            <Fact
              icon={Target}
              label="guarantee"
              value={
                result?.conformal_confidence != null
                  ? `${Math.round(result.conformal_confidence * 100)}% coverage`
                  : 'Calibrated coverage'
              }
              detail="The interval is calibrated to contain the true value at this rate."
            />
          </div>
        </CardContent>
      </Card>

      {/* Explain a prediction */}
      <Card>
        <CardHeader className="flex-row items-center gap-2 space-y-0">
          <CardTitle>Explain a prediction</CardTitle>
          <button
            type="button"
            onClick={explain}
            disabled={loading}
            className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-[0.74rem] font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <RefreshCw className="size-3.5" />
            )}
            Explain
          </button>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
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
            <p className="py-6 text-center text-sm text-danger">{error}</p>
          ) : result == null ? (
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Scoring…
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {/* Confidence — gauge + calibrated band */}
              <div className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-4">
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
              <div className="rounded-lg border border-border bg-surface p-4">
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
        </CardContent>
      </Card>
    </div>
  )
}
