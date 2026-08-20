'use client'

import { Loader2, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { ShapWaterfall } from '@/components/charts/ShapWaterfall'
import {
  EXPLAINABILITY_SOURCE,
  PANELS_ARE_DIFFERENT_MODELS,
  explainabilitySourceDetail,
} from '@/components/forecast/sources'
import { SourceLine } from '@/components/forecast/SourceLine'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Absence } from '@/components/primitives/Receipt'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { ApiError } from '@/lib/api/apiError'
import { getModelCard, mlExplain } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type { MLExplainResponse } from '@/lib/api/types'
import type { ModelCardResponse } from '@/lib/api/platform'

/** How the training frame was sourced → an honest tone, never a flattering one. */
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

const pct = (v: number): string => `${(v * 100).toFixed(1)}%`

/** One measured fact off the card — a mono label over its value. */
function Fact({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
      <span className="eyebrow">{label}</span>
      <Figure className="text-[0.95rem] leading-5 font-semibold">{value}</Figure>
      {detail ? (
        <span className="text-[0.72rem] leading-snug text-muted-foreground">{detail}</span>
      ) : null}
    </div>
  )
}

/**
 * Model explainability — the **supervised spine**, which is not the forecast above.
 *
 * The second panel of the admin forecast page (§7.15). It exists because "how does
 * the forecast look if I remove a feature" is a real question with a real answer, and
 * the answer belongs to a different model: `TrustworthyModel` has an explicit feature
 * frame and a SHAP explainer per ensemble member, while the spend forecast is a
 * univariate series with no features at all. Drawing them on one visual — the obvious
 * "simplification" — would make the product dishonest, so they are two panels with
 * two sources and a sentence between them.
 *
 * **What the waterfall explains, exactly.** The request sends no feature values, so
 * the spine imputes every one with its training median (numeric) or mode
 * (categorical) and reports them back under `imputed_features`. That makes this the
 * model's *baseline* row — derived from the training data, not typed by anyone — and
 * the panel says so rather than presenting it as a prediction about a real case. An
 * invented feature set would produce a real explanation of a fictional input, which
 * is the failure mode this page is built to avoid.
 *
 * **What is not here.** Feature selection and retraining (`POST /ml/experiment`) are
 * not built: they are a training job on the durable substrate, and an experiment must
 * never overwrite the served artifact. The panel names the gap instead of offering a
 * control that does nothing.
 */
export function ExplainabilityPanel(): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  const [card, setCard] = useState<ModelCardResponse | null>(null)
  const [result, setResult] = useState<MLExplainResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    Promise.all([getModelCard(token), mlExplain({ features: {} }, token)])
      .then(([nextCard, nextResult]) => {
        setCard(nextCard)
        setResult(nextResult)
      })
      .catch((err: unknown) => {
        // The backend's own sentence when there is no trained artifact names the
        // command that fixes it. Replacing it with "could not load" would send an
        // operator hunting for a failure that has already been explained.
        setError(
          err instanceof ApiError
            ? err.message
            : 'Could not reach the model card. Is the backend running?',
        )
      })
      .finally(() => setLoading(false))
  }, [token])

  useEffect(() => {
    if (!hydrated) return
    load()
  }, [load, hydrated])

  // SHAP is additive: base = prediction − Σ contribution (numeric targets only).
  const base = useMemo(() => {
    if (result == null || typeof result.prediction !== 'number') return 0
    return result.prediction - result.shap_attribution.reduce((s, f) => s + f.contribution, 0)
  }, [result])

  const badge = card ? dataSourceBadge(card.data_source) : null
  const imputed = result?.imputed_features?.length ?? 0
  const detail = card
    ? explainabilitySourceDetail(
        card.ensemble_members.length,
        card.conformal_predictor,
        card.training_size,
      )
    : null

  return (
    <Card>
      <CardHeader
        eyebrow="aegis.ml · the supervised spine"
        title="Model explainability"
        actions={
          <div className="flex items-center gap-2">
            {badge ? <Badge tone={badge.tone}>{badge.label}</Badge> : null}
            <button
              type="button"
              onClick={load}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50"
            >
              {loading ? (
                <Loader2 className="size-3.5 motion-safe:animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="size-3.5" aria-hidden />
              )}
              Refresh
            </button>
          </div>
        }
      />
      <CardBody className="space-y-5">
        {/*
          The sentence that keeps the two panels apart is the one piece of prose on
          this page that is *not* moved into a tooltip. Everything else here explains
          a mechanism, and DESIGN.md §4 puts that one layer down. This one prevents a
          misreading — a reader who takes these attributions as an explanation of the
          spend band above — and a warning nobody hovers is a warning nobody reads.
          It is compressed to one line and given the panel's own eyebrow instead.
        */}
        <p className="rounded-lg border border-border bg-surface-2/40 px-3.5 py-2.5 text-[0.8rem] leading-5 text-foreground">
          {PANELS_ARE_DIFFERENT_MODELS}
        </p>

        {error ? (
          <p className="py-6 text-center text-sm text-danger">{error}</p>
        ) : card == null ? (
          <div
            role="status"
            className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground"
          >
            <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden />
            Reading the model card…
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Fact
                label="features"
                value={String(card.n_features)}
                detail={`${card.categorical_features.length} categorical · ${card.numeric_features.length} numeric · ${card.encoded_feature_count} encoded columns`}
              />
              <Fact
                label="fitted on"
                value={`${card.training_size} rows`}
                detail={`${card.calibration_size} calibration · ${card.test_size ?? 0} held out`}
              />
              <Fact
                label={card.metric_name ? `held-out ${card.metric_name}` : 'held-out metric'}
                value={card.metric_value == null ? 'not measured' : card.metric_value.toFixed(3)}
                detail={
                  card.metric_value == null
                    ? 'No test split was held out, so no accuracy figure is evidence.'
                    : 'Measured on rows neither fitted nor calibrated on.'
                }
              />
              <Fact
                label="interval coverage"
                value={
                  card.conformal_coverage_empirical == null
                    ? 'not measured'
                    : pct(card.conformal_coverage_empirical)
                }
                detail={`${pct(card.conformal_coverage)} requested · ${card.conformal_predictor}`}
              />
            </div>

            {result && typeof result.prediction === 'number' ? (
              <div className="space-y-3">
                <p className="flex flex-wrap items-center gap-1.5 text-[0.78rem] text-muted-foreground">
                  <span className="eyebrow rounded-sm border border-border px-1.5 py-0.5">
                    baseline attribution
                  </span>
                  all <Figure className="text-foreground">{imputed}</Figure> features imputed
                  <InfoTip label="What a baseline attribution is">
                    Every feature sits at its training median or mode — the spine reports all{' '}
                    {imputed} of them as imputed — so this is the model&apos;s baseline
                    attribution, not a prediction about a real case.
                  </InfoTip>
                </p>
                <ShapWaterfall
                  base={base}
                  features={result.shap_attribution}
                  prediction={result.prediction}
                  maxRows={8}
                />
              </div>
            ) : null}

            <Absence
              figure="What the spine predicts without a given feature"
              why="Answering it means retraining on a feature subset and comparing against the served artifact — a training job, not a read."
              needed="POST /ml/experiment, which fits a subset spine and returns its delta without overwriting what is served."
            />
          </>
        )}

        <SourceLine source={EXPLAINABILITY_SOURCE} detail={detail} />
      </CardBody>
    </Card>
  )
}
