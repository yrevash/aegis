'use client'

import { CheckCircle2, Loader2, RefreshCw, Sigma, XCircle } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { ConformalBand } from '@/components/charts/ConformalBand'
import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { rampHex } from '@/components/charts/palette'
import { ShapWaterfall } from '@/components/charts/ShapWaterfall'
import { SceneState } from '@/components/illustration/Scene'
import { Figure } from '@/components/primitives/Figure'
import { Gauge } from '@/components/primitives/Gauge'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { getModelCard, mlExplain } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type { MLExplainRequest, MLExplainResponse } from '@/lib/api/types'
import type { ModelCardResponse } from '@/lib/api/platform'
import { cn } from '@/lib/utils'

/**
 * An example prediction to explain — these feature values drive the score the
 * backend returns.
 *
 * Every key is a name in the adapter's `FEATURES` contract
 * (`backend/src/app/adapter/ml_spec.py`). A key that is not in that contract is
 * not an error — the spine reports it under `unknown_features` and imputes the
 * training median in its place — so an invented feature set would quietly
 * explain a prediction nobody asked for.
 */
const EXAMPLE: MLExplainRequest = {
  features: {
    priority: 'high',
    category: 'billing',
    channel: 'email',
    region: 'na',
    customer_tier: 'enterprise',
    agent_tenure_months: 41,
    queue_depth_at_open: 34,
    reopened_count: 0,
    description_length: 420,
  },
}

/** The most donut slices the ordinal ramp carries before an "Other" band. */
const SLICE_CEILING = 4

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

/**
 * Figures through `Intl`, not template strings — the separators follow the
 * locale, and an explicit locale keeps the server render and the client render
 * character-identical.
 */
const PERCENT = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})

const COUNT = new Intl.NumberFormat('en-US')

/** Signed percentage points, for the measured-vs-target delta. */
const POINTS = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
  signDisplay: 'exceptZero',
})

/** A bare score — a weight, a held-out metric — at a fixed number of decimals. */
function decimals(digits: number): Intl.NumberFormat {
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

const SCORE_2 = decimals(2)
const SCORE_3 = decimals(3)

/** Percent string for a [0,1] rate. */
function pct(value: number): string {
  return PERCENT.format(value)
}

/** Hex characters of the digest shown before the ellipsis. */
const DIGEST_HEAD = 16

/**
 * Shorten a `sha256:<64 hex>` digest to something a person can read off a screen
 * and compare by eye, keeping the algorithm name.
 *
 * Truncation is for the *display* only — the full digest stays in the `title`, so
 * nothing a reader might need to paste into a comparison is lost.
 */
function shortDigest(digest: string): string {
  const [algorithm, hex] = digest.includes(':')
    ? [digest.slice(0, digest.indexOf(':')), digest.slice(digest.indexOf(':') + 1)]
    : ['', digest]
  const head = hex.slice(0, DIGEST_HEAD)
  const prefix = algorithm ? `${algorithm}:` : ''
  return hex.length > DIGEST_HEAD ? `${prefix}${head}…` : `${prefix}${hex}`
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
    <div className="flex min-w-0 flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
      <span className="eyebrow">{label}</span>
      <Figure className="min-w-0 text-[0.95rem] leading-5 font-semibold break-words">{value}</Figure>
      {detail ? (
        <span className="truncate text-[0.72rem] leading-snug text-muted-foreground" title={detail}>
          {detail}
        </span>
      ) : null}
    </div>
  )
}

/** A labelled figure in the coverage strip. */
function Stat({ label, children }: { label: string; children: ReactElement | string }): ReactElement {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-md border border-border bg-surface-2/40 p-3.5">
      <span className="eyebrow truncate">{label}</span>
      {typeof children === 'string' ? (
        <Figure size="stat" className="min-w-0 break-words">
          {children}
        </Figure>
      ) : (
        children
      )}
    </div>
  )
}

/**
 * The coverage headline: the **target** the conformal wrapper was asked for, and
 * the coverage it actually achieved on the held-out split.
 *
 * Only the target was ever rendered here, which is the weaker of the two by a
 * long way: a target is a setting, and the measured figure is the only one that
 * is evidence. `conformal_coverage_empirical`, `metric_name`, `metric_value` and
 * `test_size` were all on the wire and none of them reached the screen.
 */
function CoverageCard({ card }: { card: ModelCardResponse }): ReactElement {
  const target = card.conformal_coverage
  const measured = card.conformal_coverage_empirical
  const heldOut = card.test_size
  const deltaPp = measured == null ? null : (measured - target) * 100
  const held = deltaPp != null && deltaPp >= 0

  return (
    <Card>
      <CardHeader
        eyebrow={`${card.conformal_method} · ${card.conformal_predictor}`}
        title="Coverage — asked for, and achieved"
        actions={
          <InfoTip label="What coverage means">
            The share of held-out rows whose true value fell inside the predicted interval.
          </InfoTip>
        }
      />
      <CardBody className="@container/coverage space-y-4">
        {measured == null ? (
          <SceneState name="testing" size="md">
            <Absence
              figure="Measured coverage"
              why="Nothing was held out of the fit and the calibration."
              needed="A held-out test split."
              className="text-left"
            />
          </SceneState>
        ) : (
          <div className="flex flex-wrap items-center gap-6">
            <Gauge
              value={measured}
              color="ml"
              size={132}
              label="measured coverage"
              className="shrink-0"
            />
            <div className="grid min-w-0 flex-1 basis-[15rem] grid-cols-2 gap-3 @[44rem]/coverage:grid-cols-4">
              <Stat label="target">{pct(target)}</Stat>
              <Stat label="vs target">
                <span className="flex flex-wrap items-baseline gap-x-2">
                  <Figure size="stat" className={held ? undefined : 'text-danger'}>
                    {deltaPp == null ? '—' : `${POINTS.format(deltaPp)} pp`}
                  </Figure>
                  <span
                    className={cn(
                      'inline-flex items-center gap-1 text-[0.7rem] font-medium',
                      held ? 'text-[color:var(--ok-ink)]' : 'text-block-ink',
                    )}
                  >
                    {held ? (
                      <CheckCircle2 className="size-3.5" aria-hidden />
                    ) : (
                      <XCircle className="size-3.5" aria-hidden />
                    )}
                    {held ? 'guarantee held' : 'under target'}
                  </span>
                </span>
              </Stat>
              {heldOut != null ? (
                <Stat label="held-out rows">{COUNT.format(heldOut)}</Stat>
              ) : null}
              {card.metric_name && card.metric_value != null ? (
                <Stat label={card.metric_name}>{SCORE_3.format(card.metric_value)}</Stat>
              ) : null}
            </div>
          </div>
        )}
        <Receipt
          origin="held-out split"
          detail={[
            heldOut == null ? null : `test ${heldOut}`,
            `train ${card.training_size}`,
            `calib ${card.calibration_size}`,
          ]
            .filter(Boolean)
            .join(' · ')}
        />
      </CardBody>
    </Card>
  )
}

/**
 * MLOps — the `aegis.ml` surface: what the ensemble is, the guarantee it ships
 * and whether that guarantee held, then one prediction taken apart.
 *
 * Every figure comes straight from the accessors. Nothing here is a series:
 * the model card is a snapshot and `/ml/explain` scores one row, so the marks
 * are composition (the weight donut), position-in-a-range (the gauges and the
 * conformal band) and attribution (the waterfall) — never a trend.
 */
function MLOpsView(): ReactElement {
  // Live session token — a constant `null` would 401 on a reload and, being
  // constant in the dependency array, never retry once the session was restored.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  // ── Model card ─────────────────────────────────────────────────────────────
  const [card, setCard] = useState<ModelCardResponse | null>(null)
  const [cardError, setCardError] = useState<string | null>(null)

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    getModelCard(token)
      .then((c) => {
        if (alive) {
          setCard(c)
          setCardError(null)
        }
      })
      .catch(() => {
        if (alive) setCardError('Could not load the model card. Is the backend running?')
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

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

  // Same hydration gate: the auto-explain must not fire before the bearer exists,
  // and must re-fire once it does (`explain` is keyed on `token`).
  useEffect(() => {
    if (!hydrated) return
    explain()
  }, [explain, hydrated])

  // SHAP is additive: base = prediction − Σ contribution (numeric targets only).
  const base = useMemo(() => {
    if (result == null || typeof result.prediction !== 'number') return 0
    return result.prediction - result.shap_attribution.reduce((s, f) => s + f.contribution, 0)
  }, [result])

  /**
   * The soft-voting weights as a composition.
   *
   * A fifth member is never a fifth colour — the ordinal ramp is four steps and
   * the validator fails a fifth — so the tail folds into a named `Other (n)`
   * band whose weight really is the sum of the members it stands for.
   */
  const weightSlices: DonutDatum[] = useMemo(() => {
    const members = [...(card?.ensemble_members ?? [])].sort((a, b) => b.weight - a.weight)
    if (members.length === 0) return []
    const head = members.length > SLICE_CEILING ? members.slice(0, SLICE_CEILING - 1) : members
    const tail = members.slice(head.length)
    const rows = [
      ...head.map((m) => ({ name: m.name, value: m.weight })),
      ...(tail.length
        ? [{ name: `Other (${tail.length})`, value: tail.reduce((s, m) => s + m.weight, 0) }]
        : []),
    ]
    return rows.map((r, i) => ({
      name: r.name,
      value: r.value,
      color: 'ml' as const,
      hex: rampHex(i, rows.length),
    }))
  }, [card])

  const numeric = result != null && typeof result.prediction === 'number'
  const source = card != null ? dataSourceBadge(card.data_source) : null

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="SHAP · conformal · XGBoost + MAPIE" title="MLOps" />

      {cardError ? <ErrorState error={cardError} /> : null}

      {/* ── The guarantee, measured ─────────────────────────────────────────── */}
      {card == null && cardError == null ? (
        <Card>
          <CardBody>
            <LoadingState rows={3} label="Loading the model card…" />
          </CardBody>
        </Card>
      ) : null}

      {card ? <CoverageCard card={card} /> : null}

      {/* ── Ensemble: the weights, then the members ─────────────────────────── */}
      {card ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
          <Card className="min-w-0">
            <CardHeader eyebrow="soft voting" title="Member weights" />
            <CardBody className="space-y-4">
              {weightSlices.length ? (
                <DonutChart
                  data={weightSlices}
                  centerLabel={String(card.ensemble_members.length)}
                  centerSub="members"
                  valueFormatter={(v) => SCORE_2.format(v)}
                  height={180}
                />
              ) : (
                <Absence
                  figure="Member weights"
                  why="The card reports no ensemble member."
                  needed="A fitted ensemble."
                />
              )}
              <Receipt origin="model_card · ensemble_members[].weight" />
            </CardBody>
          </Card>

          <DataPanel
            className="min-w-0"
            eyebrow="aegis.ml"
            title="Ensemble members"
            actions={
              source ? (
                <Badge tone={source.tone} className="gap-1.5">
                  <Sigma className="size-3" aria-hidden />
                  {source.label}
                </Badge>
              ) : null
            }
            maxHeight={280}
          >
            <Table className="min-w-[26rem]">
              <THead>
                <TH>member</TH>
                <TH>kind</TH>
                <TH className="text-right">weight</TH>
              </THead>
              <TBody>
                {card.ensemble_members.map((m) => (
                  <TR key={m.name}>
                    <TD>
                      <Figure>
                        <span translate="no">{m.name}</span>
                      </Figure>
                    </TD>
                    <TD className="text-muted-foreground">{m.kind}</TD>
                    <TD className="text-right">
                      <Figure>{SCORE_2.format(m.weight)}</Figure>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </DataPanel>
        </div>
      ) : null}

      {/* ── Model card facts ────────────────────────────────────────────────── */}
      {card ? (
        <Card>
          <CardHeader eyebrow="aegis.ml" title="Model card" />
          <CardBody>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <Fact label="task" value={card.task} detail={card.target} />
              <Fact
                label="features · raw → encoded"
                value={`${card.n_features} → ${card.encoded_feature_count}`}
              />
              <Fact
                label="categorical"
                value={String(card.categorical_features.length)}
                detail={card.categorical_features.join(', ') || '—'}
              />
              <Fact
                label="numeric"
                value={String(card.numeric_features.length)}
                detail={card.numeric_features.join(', ') || '—'}
              />
            </div>

            {/*
              Training-data provenance. `data_source` (the badge above the ensemble
              table) says how the frame arrived; this says *which* frame it was —
              the fact that was missing, and without which two models fitted from
              different data, one of them poisoned, produced identical cards.
            */}
            <div className="mt-3 space-y-3">
              {card.dataset_digest ? (
                <Fact
                  label="training data · digest"
                  value={shortDigest(card.dataset_digest)}
                  detail={card.dataset_digest}
                />
              ) : (
                <Absence
                  figure="Training-data digest"
                  why="This artifact was fitted before the spine recorded one."
                  needed="A refit."
                />
              )}
              <Receipt
                origin="model_card · dataset_digest"
                detail="SHA-256 over the feature and target columns the fit consumed. It records which data produced this model, so a poisoned fit is attributable and detectable against a digest taken while the data was trusted. It prevents nothing: no frame is screened or refused."
              />
            </div>
          </CardBody>
        </Card>
      ) : null}

      {/* ── Explain a prediction ───────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          eyebrow="aegis.ml · /ml/explain"
          title="Explain a prediction"
          actions={
            <button
              type="button"
              onClick={explain}
              disabled={loading}
              aria-busy={loading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-50"
            >
              {loading ? (
                <Loader2 className="size-3.5 motion-safe:animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="size-3.5" aria-hidden />
              )}
              {loading ? 'Scoring…' : 'Explain'}
            </button>
          }
        />
        <CardBody className="space-y-4">
          {/* The input features being explained */}
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(EXAMPLE.features).map(([k, v]) => (
              <span
                key={k}
                translate="no"
                className="tabular inline-flex items-center gap-1 rounded-md bg-surface-2 px-2 py-0.5 font-mono text-[0.6875rem] text-muted-foreground"
              >
                <span className="text-foreground">{k}</span>
                <span>=</span>
                <span className="font-semibold text-foreground">{String(v)}</span>
              </span>
            ))}
          </div>

          {/* A person presses Explain and the whole readout below is replaced in
              place; without a live region a screen-reader user is told the button
              was pressed and never that an answer arrived. */}
          <div aria-live="polite" className="space-y-4">
            {error ? (
              <ErrorState error={error} />
            ) : result == null ? (
              <LoadingState rows={3} label="Scoring…" />
            ) : (
              <>
                <div className="@container/explain">
                  <div className="grid grid-cols-1 gap-5 @[46rem]/explain:grid-cols-2">
                    {/* Confidence — gauge + calibrated band */}
                    <div className="flex min-w-0 flex-col gap-4 rounded-lg border border-border bg-surface-2/40 p-4">
                      {result.conformal_confidence == null ? (
                        // This used to omit the gauge silently, leaving a hole a
                        // reader could only read as a rendering fault.
                        <Absence
                          figure="Calibrated confidence"
                          why="This prediction carries no coverage level."
                          needed="A conformal wrapper on this target."
                        />
                      ) : (
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
                    <div className="min-w-0 rounded-lg border border-border bg-surface-2/40 p-4">
                      {numeric && typeof result.prediction === 'number' ? (
                        <ShapWaterfall
                          base={base}
                          features={result.shap_attribution}
                          prediction={result.prediction}
                        />
                      ) : (
                        <SceneState name="noChart" size="sm">
                          <Absence
                            figure="Driver breakdown"
                            why="A signed waterfall needs a numeric target; this one is a class label."
                            needed="A regression target."
                            className="text-left"
                          />
                        </SceneState>
                      )}
                    </div>
                  </div>
                </div>

                {/*
                  `imputed_features` and `unknown_features` are the machine-readable
                  honesty signal on this response and nothing rendered them: a caller
                  could omit half the contract, get a confident score back, and never
                  learn that the missing half was filled from training medians.
                */}
                <Receipt
                  label="Feature intake"
                  origin={`${Object.keys(EXAMPLE.features).length} supplied · ${result.imputed_features.length} imputed · ${result.unknown_features.length} ignored`}
                  detail={
                    [
                      result.imputed_features.length
                        ? `imputed from training medians: ${result.imputed_features.join(', ')}`
                        : null,
                      result.unknown_features.length
                        ? `not model features: ${result.unknown_features.join(', ')}`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(' · ') || 'every supplied key is a model feature'
                  }
                />
              </>
            )}
          </div>
        </CardBody>
      </Card>
    </div>
  )
}

/** Client entry for the MLOps section — gated on a reachable backend. */
export function MLOpsMount(): ReactElement {
  return (
    <BackendGate>
      <MLOpsView />
    </BackendGate>
  )
}
