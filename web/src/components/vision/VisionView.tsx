'use client'

import { Coins, Eye, Loader2, ScanEye, ShieldAlert, ShieldCheck, Trash2 } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { rampHex } from '@/components/charts/palette'
import { RankedBars, type RankedDatum } from '@/components/charts/RankedBars'
import { SceneState } from '@/components/illustration/Scene'
import { Button } from '@/components/primitives/button'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { ErrorState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { ControlLadder } from '@/components/vision/ControlLadder'
import { ImageDropzone, type PickedImage } from '@/components/vision/ImageDropzone'
import { PIIOverlay } from '@/components/vision/PIIOverlay'
import { ScreenVerdictPanel } from '@/components/vision/ScreenVerdict'
import { analyseImage } from '@/lib/api/client'
import type { VisionAnalyseResponse, VisionPIIRegion } from '@/lib/api/types'
import { useAuth } from '@/lib/auth/AuthContext'

/**
 * Formatters are built once at module scope rather than per render.
 *
 * `Intl.NumberFormat` construction is the expensive half of the API — a
 * `toLocaleString()` inside a table row rebuilds one per cell, and the shipped
 * screens standardised on hoisting them so a column of figures also cannot drift
 * between two spellings of the same quantity.
 */
const COUNT = new Intl.NumberFormat('en-US')
const USD = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 5,
  maximumFractionDigits: 5,
})
const PERCENT = new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 0 })
const KILOBYTES = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })
const MEGABYTES = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })

/** Human byte size. `null` is stated, never rendered as a zero. */
function bytes(n: number | null): string {
  if (n == null) return 'unknown'
  if (n < 1024) return `${COUNT.format(n)} B`
  if (n < 1024 * 1024) return `${KILOBYTES.format(n / 1024)} KB`
  return `${MEGABYTES.format(n / (1024 * 1024))} MB`
}

/**
 * A unique, readable name per detected region.
 *
 * Two `EMAIL_ADDRESS` boxes are two different findings, and a ranked list keyed
 * on a repeated name collapses them into one row. Only the kinds that actually
 * repeat get a number, so a single finding stays plainly `EMAIL_ADDRESS`.
 */
function regionLabels(regions: VisionPIIRegion[]): string[] {
  const total = new Map<string, number>()
  for (const r of regions) total.set(r.entity_type, (total.get(r.entity_type) ?? 0) + 1)
  const seen = new Map<string, number>()
  return regions.map((r) => {
    const nth = (seen.get(r.entity_type) ?? 0) + 1
    seen.set(r.entity_type, nth)
    return (total.get(r.entity_type) ?? 0) > 1 ? `${r.entity_type} ${nth}` : r.entity_type
  })
}

/**
 * Aegis Vision — image understanding with the injection screen ahead of the model.
 *
 * The page is arranged to make one claim legible: an image is refused *before*
 * the answering model is called if it carries instructions aimed at an AI. So the
 * screen verdict sits at the top of the result column at full width, the control
 * ladder below it reads top-to-bottom in execution order (including the controls
 * that did not run), and the analysis — the thing every other product would lead
 * with — comes last, because on a blocked run there is none.
 *
 * **This is a single-run snapshot, so nothing here is a trend.** The response
 * carries no timestamps and no history; the two marks are compositions of one
 * call — prompt against completion tokens, and the detected PII regions — and
 * the spatial truth (where the PII actually is) is drawn by `PIIOverlay` on the
 * image itself rather than restated as a chart.
 */
function VisionView(): ReactElement {
  // No `hydrated` gate here: nothing fetches on mount. The analysis only fires on
  // a click, by which point the session is restored — and `request` falls back to
  // the persisted bearer anyway, so a null here never sends an unauthenticated call.
  const { session } = useAuth()
  const token = session?.token ?? null

  const [image, setImage] = useState<PickedImage | null>(null)
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState<VisionAnalyseResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  function pick(picked: PickedImage): void {
    setImage(picked)
    setResult(null)
    setError(null)
  }

  function clear(): void {
    setImage(null)
    setResult(null)
    setError(null)
  }

  function run(): void {
    if (image == null || running) return
    setRunning(true)
    setError(null)
    analyseImage(
      {
        image_base64: image.base64,
        mime_type: image.mime,
        question,
        filename: image.name,
      },
      token,
    )
      .then(setResult)
      .catch(() => setError('Could not analyse that image. Is the backend running?'))
      .finally(() => setRunning(false))
  }

  const analysis = result?.analysis ?? null
  const blocked = analysis?.outcome === 'blocked'
  const usage = analysis?.usage ?? null
  const regions = analysis?.pii_regions ?? []

  // Prompt against completion on the one call that was made. Two slices, ranked,
  // so the ramp is sampled at its two ends rather than at two adjacent steps.
  const promptTokens = usage?.prompt_tokens ?? 0
  const completionTokens = usage?.completion_tokens ?? 0
  const totalTokens = promptTokens + completionTokens
  const tokenSplit: DonutDatum[] = [
    { name: 'prompt', value: promptTokens },
    { name: 'completion', value: completionTokens },
  ]
    .sort((a, b) => b.value - a.value)
    .map((d, i) => ({ ...d, color: 'graph' as const, hex: rampHex(i, 2) }))

  // Presidio reports a score per detection *when it has one*. The rows that do
  // are charted; a run where none does falls back to the counts, which are always
  // real, and states the missing confidence rather than drawing it as zero.
  const labels = regionLabels(regions)
  const scored: RankedDatum[] = regions
    .map((r, i) => ({ region: r, name: labels[i] }))
    .filter((row) => row.region.score != null)
    .map((row) => ({ name: row.name, value: row.region.score as number }))
  const byKind: RankedDatum[] = Object.entries(
    regions.reduce<Record<string, number>>((acc, r) => {
      acc[r.entity_type] = (acc[r.entity_type] ?? 0) + 1
      return acc
    }, {}),
  ).map(([name, value]) => ({ name, value }))

  return (
    <div className="min-w-0 space-y-6">
      <PageHeader
        eyebrow="screen · then model"
        title="Vision"
        actions={
          <>
            {image != null ? (
              <Button variant="outline" onClick={clear} disabled={running}>
                <Trash2 className="size-4" aria-hidden /> Clear
              </Button>
            ) : null}
            <Button onClick={run} disabled={running || image == null}>
              {running ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden /> Screening…
                </>
              ) : (
                <>
                  <ScanEye className="size-4" aria-hidden /> Screen &amp; analyse
                </>
              )}
            </Button>
          </>
        }
      />

      {/* The run is asynchronous and its result lands far down the page; this is
          the only thing a screen-reader user is told about it, so it says the
          outcome rather than re-reading the panels. */}
      <p aria-live="polite" className="sr-only">
        {running
          ? 'Screening the image.'
          : analysis == null
            ? ''
            : blocked
              ? `Blocked at ${analysis.blocked_stage ?? 'a control'}.`
              : 'Analysis complete.'}
      </p>

      {/* Headline figures — only once there is something measured to show. */}
      {analysis != null && usage != null ? (
        <div className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label="Outcome"
            value={blocked ? 'BLOCKED' : 'ANSWERED'}
            icon={blocked ? ShieldAlert : ShieldCheck}
            tone={blocked ? 'block' : 'ok'}
          />
          <StatCard
            label="Injection screen"
            value={
              analysis.screen == null
                ? 'not reached'
                : !analysis.screen.screened
                  ? 'could not run'
                  : analysis.screen.injection
                    ? 'injection found'
                    : 'clear'
            }
            icon={ScanEye}
            tone={
              analysis.screen == null || !analysis.screen.screened
                ? 'risk'
                : analysis.screen.injection
                  ? 'block'
                  : 'ok'
            }
          />
          <StatCard
            label="PII regions found"
            value={COUNT.format(regions.length)}
            icon={Eye}
            tone={regions.length > 0 ? 'ml' : 'neutral'}
          />
          {/* `unpriced` means billable work nobody could price. Rendering it as
              `$0.00000` would assert the call was free, which is a different
              statement — so the tile becomes the stated absence in its place. */}
          {usage.cost_source === 'unpriced' ? (
            <Absence
              figure="Call cost"
              why="The deployment reported no price for this model, so the call is unpriced rather than free."
              needed="a rate for this model in the gateway's price table"
            />
          ) : (
            <StatCard
              label="Call cost"
              value={USD.format(usage.cost_usd)}
              icon={Coins}
              tone="neutral"
              source={`analysis.usage · ${COUNT.format(usage.images)} image${usage.images === 1 ? '' : 's'} · ${usage.cost_source}`}
            />
          )}
        </div>
      ) : null}

      <div className="grid min-w-0 grid-cols-1 gap-6 lg:grid-cols-12">
        {/* ── Input column ─────────────────────────────────────────────────── */}
        <div className="min-w-0 space-y-6 lg:col-span-5">
          <Card className="min-w-0">
            <CardHeader
              eyebrow="upload"
              title="Image"
              actions={
                image != null ? (
                  <Badge tone="neutral" className="font-mono text-[0.66rem]">
                    {COUNT.format(image.width)}×{COUNT.format(image.height)} · {bytes(image.bytes)}
                  </Badge>
                ) : null
              }
            />
            <CardBody className="min-w-0 pt-4">
              {image == null ? (
                <ImageDropzone onPick={pick} onError={setError} disabled={running} />
              ) : (
                <PIIOverlay
                  src={image.dataUrl}
                  alt={image.name}
                  facts={analysis?.image ?? null}
                  regions={regions}
                />
              )}
            </CardBody>
          </Card>

          <Card className="min-w-0">
            <CardHeader
              eyebrow="prompt"
              title="Question"
              actions={
                <InfoTip label="Why the screen runs before the model">
                  Text rendered inside an image is read by a vision model as if it had been
                  typed.
                </InfoTip>
              }
            />
            <CardBody className="min-w-0 pt-4">
              <label htmlFor="vision-question" className="sr-only">
                Question for the vision model
              </label>
              <textarea
                id="vision-question"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                rows={3}
                placeholder="Describe this image."
                className="w-full resize-y rounded-lg border border-border bg-surface-2/40 px-3.5 py-2.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus-visible:border-blue-400 focus-visible:ring-2 focus-visible:ring-ring"
              />
            </CardBody>
          </Card>
        </div>

        {/* ── Result column ────────────────────────────────────────────────── */}
        <div className="min-w-0 space-y-6 lg:col-span-7">
          {error != null ? <ErrorState error={error} /> : null}

          {analysis == null ? (
            <Card className="min-w-0">
              <CardBody>
                {/* "Give the platform a document" before there is one; "somebody
                    with a question, before they ask it" once there is. */}
                <SceneState name={image == null ? 'upload' : 'curious'} size="md">
                  <p className="text-sm text-muted-foreground">
                    {image == null
                      ? 'Drop an image in to run the screen.'
                      : 'Screen and analyse to see the verdict, the controls and the cost.'}
                  </p>
                </SceneState>
              </CardBody>
            </Card>
          ) : (
            <>
              {/* The differentiator, at the top and at full width. */}
              <ScreenVerdictPanel verdict={analysis.screen} />

              <Card className="min-w-0">
                <CardHeader
                  eyebrow="aegis.vision · execution order"
                  title="Controls"
                  actions={
                    <Badge tone={blocked ? 'block' : 'ok'} className="uppercase">
                      {blocked ? `refused at ${analysis.blocked_stage ?? 'a control'}` : 'answered'}
                    </Badge>
                  }
                />
                <CardBody className="min-w-0 space-y-4 pt-4">
                  <ControlLadder controls={analysis.controls} />
                  {result?.coverage ? (
                    <Receipt label="Coverage" origin={result.coverage} />
                  ) : null}
                </CardBody>
              </Card>

              {/* ── The two honest marks on one call ───────────────────────── */}
              <div className="@container min-w-0">
                <div className="grid min-w-0 items-start gap-6 @2xl:grid-cols-2">
                  <Card className="flex min-w-0 flex-col">
                    <CardHeader as="h3" eyebrow="analysis.usage" title="Tokens on the call" />
                    <CardBody className="flex min-h-0 min-w-0 flex-1 flex-col gap-4">
                      {totalTokens === 0 ? (
                        <Absence
                          figure="Token split"
                          why={
                            blocked
                              ? 'The run was refused before the analysing model was called.'
                              : 'The provider reported no token counts for this call.'
                          }
                        />
                      ) : (
                        <div className="min-w-0">
                          <DonutChart
                            data={tokenSplit}
                            height={180}
                            centerLabel={COUNT.format(totalTokens)}
                            centerSub="tokens"
                            valueFormatter={(v) => COUNT.format(v)}
                          />
                        </div>
                      )}
                      <Receipt
                        className="mt-auto"
                        origin={`analysis.usage · ${usage?.model || 'no model call'}`}
                      />
                    </CardBody>
                  </Card>

                  <Card className="flex min-w-0 flex-col">
                    <CardHeader
                      as="h3"
                      eyebrow="analysis.pii_regions"
                      title="Detected PII"
                      actions={
                        <Badge tone={regions.length > 0 ? 'ml' : 'neutral'} className="font-mono">
                          {COUNT.format(regions.length)}
                        </Badge>
                      }
                    />
                    <CardBody className="flex min-h-0 min-w-0 flex-1 flex-col gap-4">
                      {regions.length === 0 ? (
                        /* Measured, and the measurement was zero — a scene rather
                           than an `Absence`, which would claim nothing looked. */
                        <SceneState name="empty" size="sm">
                          <p className="text-sm text-muted-foreground">
                            The image-PII control found no regions.
                          </p>
                        </SceneState>
                      ) : scored.length > 0 ? (
                        <RankedBars
                          label="Detection confidence per region"
                          data={scored}
                          valueFormatter={(v) => PERCENT.format(v)}
                          color="ml"
                          maxRows={8}
                          tail="omit"
                        />
                      ) : (
                        <>
                          <RankedBars
                            label="Regions per entity kind"
                            data={byKind}
                            valueFormatter={(v) => COUNT.format(v)}
                            color="ml"
                            maxRows={8}
                          />
                          <Absence
                            figure="Detection confidence"
                            why="Presidio reported no score for these regions."
                          />
                        </>
                      )}
                      {regions.length > 0 ? (
                        <Receipt
                          className="mt-auto"
                          origin={
                            scored.length > 0
                              ? 'analysis.pii_regions[].score · presidio'
                              : 'analysis.pii_regions[].entity_type'
                          }
                        />
                      ) : null}
                    </CardBody>
                  </Card>
                </div>
              </div>

              <Card className="min-w-0">
                <CardHeader
                  eyebrow={
                    usage?.model
                      ? `${usage.model} · ${COUNT.format(promptTokens)}+${COUNT.format(completionTokens)} tok`
                      : 'no model call was made'
                  }
                  title="Analysis"
                  actions={
                    analysis.output != null ? (
                      <Badge
                        tone={analysis.output.verdict === 'pass' ? 'ok' : 'block'}
                        className="uppercase"
                      >
                        rails · {analysis.output.verdict}
                      </Badge>
                    ) : null
                  }
                />
                <CardBody className="min-w-0 pt-4">
                  {blocked ? (
                    <p className="text-sm leading-relaxed break-words text-block-ink">
                      {analysis.blocked_reason}
                    </p>
                  ) : (
                    <p className="text-sm leading-relaxed break-words whitespace-pre-wrap text-foreground">
                      {analysis.answer}
                    </p>
                  )}
                  {analysis.pii_entities.length > 0 ? (
                    <p className="mt-4 flex flex-wrap items-center gap-1.5 text-[0.72rem] text-muted-foreground">
                      <span>redacted before the model saw it:</span>
                      {analysis.pii_entities.map((kind) => (
                        <Badge key={kind} tone="ml" className="font-mono text-[0.66rem]">
                          {kind}
                        </Badge>
                      ))}
                    </p>
                  ) : null}
                  {/* Mime, size and provenance are the receipt; the mechanism —
                      why a declared type is never believed — is the tooltip
                      `Receipt` opens for any detail longer than a measured fact. */}
                  {analysis.image != null ? (
                    <Receipt
                      className="mt-5"
                      origin={`analysis.image · declared ${analysis.image.declared_mime}, sniffed ${analysis.image.sniffed_mime ?? 'unrecognised'} · ${bytes(analysis.image.byte_size)} · provenance ${analysis.image.provenance}`}
                      detail="The declared type is attacker-controlled, so payload hygiene compares it against the file's magic bytes."
                    />
                  ) : null}
                </CardBody>
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/** Client entry for the Vision section — gated on a reachable backend. */
export function VisionMount(): ReactElement {
  return (
    <BackendGate>
      <VisionView />
    </BackendGate>
  )
}
