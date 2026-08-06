import { TrendingUp } from 'lucide-react'
import { useMemo, type ReactElement } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { InfoTip } from '@/components/ui/InfoTip'
import type { MLExplanation } from '@/types/stream'

import { ShapBar } from './ShapBar'

/** How many top drivers to surface on the primary rail (§4.1). */
const TOP_N = 3

/**
 * "Why" — the top drivers of the model's score. Shows the three
 * highest-magnitude drivers as bars that raise (green) or lower (red) the score.
 * The calibrated confidence band lives in its own Confidence card now; the full
 * driver list and the honest "SHAP attribution" term live one layer down in the
 * ⓘ tooltip, keeping the primary surface plain-language.
 */
export function ShapPanel({ ml }: { ml: MLExplanation | null }): ReactElement {
  const sorted = useMemo(
    () =>
      ml
        ? [...ml.shap_attribution].sort(
            (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution),
          )
        : [],
    [ml],
  )
  const top = sorted.slice(0, TOP_N)
  const more = sorted.length - top.length
  const maxAbs = useMemo(
    () => top.reduce((m, f) => Math.max(m, Math.abs(f.contribution)), 0),
    [top],
  )

  return (
    <Card className="flex h-full flex-col">
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <CardTitle>Why</CardTitle>
        <InfoTip label="About Why">
          The top drivers of the model&rsquo;s score (SHAP feature attribution).
          Green bars raise the score, red bars lower it.
        </InfoTip>
        {ml && (
          <span className="tabular ml-auto font-display text-lg font-semibold text-ml-ink">
            {typeof ml.prediction === 'number' ? ml.prediction.toFixed(2) : ml.prediction}
          </span>
        )}
      </CardHeader>
      <CardContent className="min-h-0 flex-1">
        {top.length === 0 ? (
          <div className="flex h-full min-h-24 flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
            <TrendingUp className="size-6 text-muted-foreground/50" />
            <p>Top drivers appear once the model scores.</p>
          </div>
        ) : (
          <>
            <div className="space-y-2">
              {top.map((f) => (
                <ShapBar key={f.feature} feature={f} maxAbs={maxAbs} />
              ))}
            </div>
            <div className="mt-3 flex items-center justify-between font-mono text-[0.64rem] text-muted-foreground">
              <span className="text-block-ink">&#9668; lowers</span>
              {more > 0 && <span className="text-muted-foreground/70">+{more} more</span>}
              <span className="text-ok-ink">raises &#9658;</span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
