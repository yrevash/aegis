import { ChevronDown, Gauge as GaugeIcon } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Gauge } from '@/components/ui/Gauge'
import { InfoTip } from '@/components/ui/InfoTip'
import { cn } from '@/lib/utils'
import type { MLExplanation } from '@/types/stream'

import { ConformalInterval } from './ConformalInterval'
import { conformalSignals } from './conformalScale'

/**
 * "Confidence" — the calibrated confidence level as a single radial gauge
 * (§4.1). The raw numeric interval, its width, and the prediction-set size (the
 * conformal machinery) are demoted: the plain percentage leads, and the band
 * itself is one click away under "Technical detail". Draws in on first result.
 */
export function ConfidenceCard({
  ml,
  unit,
}: {
  ml: MLExplanation | null
  unit?: string
}): ReactElement {
  const [open, setOpen] = useState(false)
  const confidence = ml?.conformal_confidence ?? null
  // Collect the band once so the render narrows cleanly (no non-null bangs).
  const band =
    ml && typeof ml.prediction === 'number' && ml.conformal_interval != null
      ? {
          prediction: ml.prediction,
          interval: ml.conformal_interval,
          confidence: ml.conformal_confidence,
          width: ml.interval_width,
          setSize: ml.prediction_set_size,
        }
      : null
  const { widthLabel, setLabel } = conformalSignals(ml?.interval_width, ml?.prediction_set_size, unit)

  return (
    <Card className="flex h-full flex-col">
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <CardTitle>Confidence</CardTitle>
        <InfoTip label="About Confidence">
          A calibrated confidence level with guaranteed coverage (conformal
          prediction, MAPIE). The prediction band and its width live under
          &ldquo;Technical detail&rdquo;.
        </InfoTip>
      </CardHeader>
      <CardContent className="min-h-0 flex-1">
        {confidence == null ? (
          <div className="flex h-full min-h-28 flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
            <GaugeIcon className="size-6 text-muted-foreground/50" />
            <p>The model&rsquo;s confidence appears once it scores.</p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Gauge value={confidence} color="ml" size={132} label="calibrated" />
            {(widthLabel || setLabel) && (
              <p className="tabular text-center font-mono text-[0.66rem] text-muted-foreground">
                {[widthLabel, setLabel].filter(Boolean).join(' · ')}
              </p>
            )}
            {band && (
              <div className="w-full">
                <button
                  type="button"
                  onClick={() => setOpen((v) => !v)}
                  aria-expanded={open}
                  className="mx-auto flex items-center gap-1 text-[0.72rem] text-muted-foreground transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <ChevronDown className={cn('size-3 transition-transform', open && 'rotate-180')} />
                  Technical detail
                </button>
                {open && (
                  <div className="animate-reveal mt-2">
                    <ConformalInterval
                      prediction={band.prediction}
                      interval={band.interval}
                      confidence={band.confidence}
                      width={band.width}
                      setSize={band.setSize}
                      unit={unit}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
