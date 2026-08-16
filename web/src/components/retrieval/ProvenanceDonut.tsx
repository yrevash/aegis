'use client'

import type { ReactElement } from 'react'

import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { chartHex } from '@/components/charts/palette'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import type { RetrievalOrigin } from '@/lib/stream'
import { ORIGIN_COLOR, ORIGIN_LABEL, type RetrievalObservability } from './observability'

export interface ProvenanceDonutProps {
  obs: RetrievalObservability
}

/**
 * The provenance donut — the origins mix (vector / graph / bm25) that fed the
 * fused pool, with the fusion method (RRF) read out at the centre. Slices are
 * weighted by each arm's measured candidate count when the stream carries one;
 * the `/query` SSE contract does not, so fired arms are weighted equally and the
 * panel says so rather than implying a measured split.
 */
export function ProvenanceDonut({ obs }: ProvenanceDonutProps): ReactElement {
  const hasCounts = obs.arms.some((a) => a.candidates > 0)
  const slices = obs.arms
    .map((arm) => {
      const origin = (arm.origins[0] ?? 'vector') as RetrievalOrigin
      const value = hasCounts ? arm.candidates : arm.fired ? 1 : 0
      return { origin, value }
    })
    .filter((s) => s.value > 0)

  const data: DonutDatum[] = slices.map((s) => ({
    name: ORIGIN_LABEL[s.origin],
    value: s.value,
    color: ORIGIN_COLOR[s.origin],
  }))

  const fusionLabel = obs.fusion === 'none' ? 'single' : obs.fusion.toUpperCase()

  return (
    <Card>
      <CardHeader
        eyebrow="origins mix"
        title="Provenance"
        description="Which recall arms fed the fused pool, and how they were combined."
        actions={
          <Badge tone="graph" className="uppercase">
            fusion · {fusionLabel}
          </Badge>
        }
      />
      <CardBody className="pt-4">
        {data.length === 0 ? (
          <div className="flex min-h-40 items-center justify-center text-sm text-muted-foreground">
            No recall arms fired yet.
          </div>
        ) : (
          <>
            <DonutChart
              data={data}
              centerLabel={fusionLabel}
              centerSub={`${obs.fused_candidates} fused`}
              valueFormatter={(v) => (hasCounts ? `${v} candidates` : 'fired')}
              height={200}
            />
            <ul className="mt-3 space-y-1.5">
              {slices.map((s) => (
                <li key={s.origin} className="flex items-center gap-2 text-sm">
                  <span
                    className="size-2.5 shrink-0 rounded-full"
                    style={{ background: chartHex(ORIGIN_COLOR[s.origin]) }}
                  />
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {ORIGIN_LABEL[s.origin]}
                  </span>
                  <span className="tabular-nums font-mono text-[0.72rem] text-foreground">
                    {hasCounts ? s.value : 'fired'}
                  </span>
                </li>
              ))}
            </ul>
            {!hasCounts && (
              <p className="mt-2 text-[0.7rem] text-muted-foreground/80">
                Per-arm counts are not carried on the /query stream — slices weight fired arms
                equally.
              </p>
            )}
          </>
        )}
      </CardBody>
    </Card>
  )
}
