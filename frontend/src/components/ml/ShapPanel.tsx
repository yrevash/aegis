import { TrendingUp } from 'lucide-react'
import { useMemo, type ReactElement } from 'react'

import { ShapWaterfall } from '@/components/charts/ShapWaterfall'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { InfoTip } from '@/components/ui/InfoTip'
import type { MLExplanation } from '@/types/stream'

import { ShapBar } from './ShapBar'

/**
 * "Why" — the full driver breakdown behind the model's prediction, drawn as a
 * complete SHAP waterfall (base → every signed driver → prediction) when the
 * target is numeric: raises are warm, lowers cool, and every figure sits in an
 * aligned tabular-mono column so nothing is clipped inside a bar. For a
 * non-numeric (classification) target we fall back to magnitude bars, since a
 * cumulative value walk has no meaning there. The honest "SHAP attribution"
 * term lives one layer down in the ⓘ tooltip.
 */
export function ShapPanel({ ml, unit }: { ml: MLExplanation | null; unit?: string }): ReactElement {
  const sorted = useMemo(
    () =>
      ml
        ? [...ml.shap_attribution].sort(
            (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution),
          )
        : [],
    [ml],
  )

  // SHAP is additive: prediction = base + Σ contribution, so the base value the
  // waterfall walks from is prediction − Σ contribution (only defined when the
  // prediction is numeric).
  const numeric = ml != null && typeof ml.prediction === 'number'
  const base = useMemo(() => {
    if (ml == null || typeof ml.prediction !== 'number') return 0
    return ml.prediction - sorted.reduce((s, f) => s + f.contribution, 0)
  }, [ml, sorted])

  const maxAbs = useMemo(
    () => sorted.reduce((m, f) => Math.max(m, Math.abs(f.contribution)), 0),
    [sorted],
  )

  return (
    <Card className="flex h-full flex-col">
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <CardTitle>Why</CardTitle>
        <InfoTip label="About Why">
          The full driver breakdown behind the model&rsquo;s prediction (SHAP
          feature attribution). Each driver raises or lowers the prediction; the
          bars add up from the base value to the final number.
        </InfoTip>
      </CardHeader>
      <CardContent className="min-h-0 flex-1">
        {sorted.length === 0 || ml == null ? (
          <div className="flex h-full min-h-24 flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
            <TrendingUp className="size-6 text-muted-foreground/50" />
            <p>The driver breakdown appears once the model scores.</p>
          </div>
        ) : numeric && typeof ml.prediction === 'number' ? (
          <ShapWaterfall base={base} features={sorted} prediction={ml.prediction} unit={unit} />
        ) : (
          <div className="space-y-2">
            {sorted.map((f) => (
              <ShapBar key={f.feature} feature={f} maxAbs={maxAbs} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
