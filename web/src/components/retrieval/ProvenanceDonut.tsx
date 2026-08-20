'use client'

import type { ReactElement } from 'react'

import { RankedBars, type RankedDatum } from '@/components/charts/RankedBars'
import { Figure } from '@/components/primitives/Figure'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import type { RetrievalOrigin } from '@/lib/stream'
import { ORIGIN_LABEL, type RetrievalObservability } from './observability'

export interface ProvenanceDonutProps {
  obs: RetrievalObservability
}

/**
 * The provenance donut — the origins mix (vector / graph / bm25) that fed the
 * fused pool, with the fusion method (RRF) read out at the centre.
 *
 * **It is no longer a donut.** Three arms need three distinguishable categorical
 * colours, and this system has one hue: `scripts/validate_palette.js` fails the
 * three-step ramp on its lightness band (`#0b3b8f` at L 0.38, below the 0.43
 * floor), because a set spanning from tint to near-ink reads as emphasis rather
 * than as three of a kind. Ranked bars need no categorical palette at all —
 * identity comes from the label and the ordering, which is DESIGN.md §2's stated
 * first resort — and they answer "which arm contributed most" far better than
 * three angles do. The fused total, which is what the centre hole used to hold,
 * is a figure and now reads as one.
 *
 * **The split is drawn only when it was measured.** Slices are weighted by
 * each arm's candidate count, and the `/query` SSE contract does not carry one.
 * The panel used to handle that by weighting every fired arm equally and adding a
 * footnote — but an equal split *is a claim about proportions*, drawn at full
 * confidence, and a footnote under a circle does not survive a screenshot. Three
 * arms firing renders as three identical thirds, which is a measurement no one
 * took. So the unmeasured case now states the absence in the slot the chart would
 * have occupied (DESIGN.md §1) and names the arms that did fire, which is the
 * part the stream actually evidences.
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

  const data: RankedDatum[] = slices.map((s) => ({
    name: ORIGIN_LABEL[s.origin],
    value: s.value,
  }))

  const fusionLabel = obs.fusion === 'none' ? 'single' : obs.fusion.toUpperCase()

  return (
    <Card>
      <CardHeader
        eyebrow="origins mix"
        title="Provenance"
        actions={
          <Badge tone="graph" className="uppercase">
            fusion · {fusionLabel}
          </Badge>
        }
      />
      <CardBody className="space-y-3 pt-4">
        {data.length === 0 ? (
          <div className="flex min-h-40 items-center justify-center text-sm text-muted-foreground">
            No recall arms fired yet.
          </div>
        ) : hasCounts ? (
          <>
            <div>
              <p className="eyebrow">candidates in the fused pool</p>
              <p className="mt-1">
                <Figure size="stat">{obs.fused_candidates}</Figure>
              </p>
            </div>
            <RankedBars
              label="Candidates per recall arm, most first"
              data={data}
              valueFormatter={(v) => `${v} candidates`}
            />
            <Receipt
              origin="/query stream · per-arm candidate counts"
              detail={`${obs.fused_candidates} candidates in the fused pool · ${fusionLabel}`}
            />
          </>
        ) : (
          <>
            <Absence
              figure="How the fused pool split across the recall arms"
              why="The /query SSE contract reports which arms fired but not how many candidates each returned, so any split drawn here would be an equal one — a shape, not a measurement."
              needed="A per-arm candidate count on the retrieval event, emitted where the arms are fused."
            />
            <div>
              <p className="eyebrow mb-1.5">arms that fired</p>
              <ul className="flex flex-wrap gap-1.5">
                {slices.map((s) => (
                  <li
                    key={s.origin}
                    className="rounded-md border border-border bg-surface-2/40 px-2 py-1 text-[0.72rem] text-foreground"
                  >
                    {ORIGIN_LABEL[s.origin]}
                  </li>
                ))}
              </ul>
            </div>
            <Receipt
              origin={`/query stream · ${fusionLabel} fusion`}
              detail={`${obs.fused_candidates} candidates in the fused pool`}
            />
          </>
        )}
      </CardBody>
    </Card>
  )
}
