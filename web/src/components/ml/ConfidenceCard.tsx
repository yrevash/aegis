'use client'

import { Gauge as GaugeIcon } from 'lucide-react'
import { type ReactElement } from 'react'

import { ConformalBand } from '@/components/charts/ConformalBand'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { Gauge } from '@/components/primitives/Gauge'
import { InfoTip } from '@/components/primitives/InfoTip'
import type { MLExplanation } from '@/lib/stream'

/**
 * "Confidence" — the calibrated confidence level as a radial gauge over the
 * conformal band itself. The plain percentage leads (guaranteed coverage via
 * conformal prediction, MAPIE); directly beneath it the ConformalBand draws the
 * calibrated interval on a data-derived number line so the actual bounds are
 * visible, not hidden a click away. Draws in on first result.
 */
export function ConfidenceCard({
  ml,
  unit,
}: {
  ml: MLExplanation | null
  unit?: string
}): ReactElement {
  const confidence = ml?.conformal_confidence ?? null

  return (
    <Card className="flex h-full flex-col">
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <CardTitle>Confidence</CardTitle>
        <InfoTip label="About Confidence">
          A calibrated confidence level with guaranteed coverage (conformal
          prediction, MAPIE). The band below shows the calibrated interval around
          the prediction; a wider band means less certainty.
        </InfoTip>
      </CardHeader>
      <CardContent className="min-h-0 flex-1">
        {ml == null ? (
          <div className="flex h-full min-h-28 flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
            <GaugeIcon className="size-6 text-muted-foreground/50" />
            <p>The model&rsquo;s confidence appears once it scores.</p>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {confidence != null && (
              <div className="flex justify-center">
                <Gauge value={confidence} color="ml" size={124} label="calibrated" />
              </div>
            )}
            <ConformalBand
              prediction={ml.prediction}
              interval={ml.conformal_interval}
              confidence={ml.conformal_confidence}
              intervalWidth={ml.interval_width}
              setSize={ml.prediction_set_size}
              unit={unit}
            />
          </div>
        )}
      </CardContent>
    </Card>
  )
}