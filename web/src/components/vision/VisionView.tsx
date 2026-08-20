'use client'

import {
  Coins,
  Eye,
  Loader2,
  ScanEye,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { PageHeader } from '@/components/primitives/PageHeader'
import { StatCard } from '@/components/ui/StatCard'
import { ControlLadder } from '@/components/vision/ControlLadder'
import { ImageDropzone, type PickedImage } from '@/components/vision/ImageDropzone'
import { PIIOverlay } from '@/components/vision/PIIOverlay'
import { ScreenVerdictPanel } from '@/components/vision/ScreenVerdict'
import { analyseImage } from '@/lib/api/client'
import type { VisionAnalyseResponse } from '@/lib/api/types'
import { useAuth } from '@/lib/auth/AuthContext'

/** Format a per-call cost without pretending an unpriced call was free. */
function costLabel(cost: number, source: string): string {
  if (source === 'unpriced') return 'unpriced'
  return `$${cost.toFixed(5)}`
}

/** Human byte size. */
function bytes(n: number | null): string {
  if (n == null) return 'unknown'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(2)} MB`
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

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="screen · then model"
        title="Vision"
        actions={
          <>
            {image != null ? (
              <button
                type="button"
                onClick={clear}
                disabled={running}
                className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3.5 py-2.5 text-sm font-medium text-muted-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
              >
                <Trash2 className="size-4" /> Clear
              </button>
            ) : null}
            <button
              type="button"
              onClick={run}
              disabled={running || image == null}
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {running ? (
                <>
                  <Loader2 className="size-4 animate-spin" /> Screening…
                </>
              ) : (
                <>
                  <ScanEye className="size-4" /> Screen &amp; analyse
                </>
              )}
            </button>
          </>
        }
      />

      {/* Headline figures — only once there is something measured to show. */}
      {analysis != null ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
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
            value={String(analysis.pii_regions.length)}
            icon={Eye}
            tone={analysis.pii_regions.length > 0 ? 'ml' : 'neutral'}
          />
          <StatCard
            label="Call cost"
            value={costLabel(analysis.usage.cost_usd, analysis.usage.cost_source)}
            icon={Coins}
            tone="neutral"
          />
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* ── Input column ─────────────────────────────────────────────────── */}
        <div className="space-y-6 lg:col-span-5">
          <Card>
            <CardHeader
              eyebrow="upload"
              title="Image"
              actions={
                image != null ? (
                  <Badge tone="neutral" className="font-mono text-[0.66rem]">
                    {image.width}×{image.height} · {bytes(image.bytes)}
                  </Badge>
                ) : null
              }
            />
            <CardBody className="pt-4">
              {image == null ? (
                <ImageDropzone onPick={pick} onError={setError} disabled={running} />
              ) : (
                <PIIOverlay
                  src={image.dataUrl}
                  alt={image.name}
                  facts={analysis?.image ?? null}
                  regions={analysis?.pii_regions ?? []}
                />
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader eyebrow="prompt" title="Question" />
            <CardBody className="space-y-3 pt-4">
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                rows={3}
                placeholder="Describe this image."
                className="w-full resize-y rounded-lg border border-border bg-surface-2/40 px-3.5 py-2.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-blue-200"
              />
              <p className="text-[0.72rem] leading-snug text-muted-foreground">
                Text rendered inside an image is read by a vision model as if it had been typed.
                The screen looks for exactly that before the analysing model is called.
              </p>
            </CardBody>
          </Card>
        </div>

        {/* ── Result column ────────────────────────────────────────────────── */}
        <div className="space-y-6 lg:col-span-7">
          {error != null ? (
            <Card>
              <CardBody>
                <p className="py-6 text-center text-sm text-danger">{error}</p>
              </CardBody>
            </Card>
          ) : null}

          {analysis == null ? (
            <Card>
              <CardBody>
                <div className="flex flex-col items-center gap-2 py-14 text-center">
                  <Sparkles className="size-6 text-muted-foreground" />
                  <p className="text-sm text-muted-foreground">
                    {image == null
                      ? 'No image yet. Drop one in to run the screen.'
                      : 'Nothing analysed yet — no screen verdict, no controls, no cost.'}
                  </p>
                </div>
              </CardBody>
            </Card>
          ) : (
            <>
              {/* The differentiator, at the top and at full width. */}
              <ScreenVerdictPanel verdict={analysis.screen} />

              <Card>
                <CardHeader
                  eyebrow="aegis.vision · execution order"
                  title="Controls"
                  actions={
                    <Badge tone={blocked ? 'block' : 'ok'} className="uppercase">
                      {blocked ? `refused at ${analysis.blocked_stage ?? 'a control'}` : 'answered'}
                    </Badge>
                  }
                />
                <CardBody className="pt-4">
                  <ControlLadder controls={analysis.controls} />
                  {result?.coverage ? (
                    <p className="mt-5 rounded-lg bg-surface-2/60 px-3 py-2 text-[0.74rem] leading-snug text-muted-foreground">
                      {result.coverage}
                    </p>
                  ) : null}
                </CardBody>
              </Card>

              <Card>
                <CardHeader
                  eyebrow={
                    analysis.usage.model
                      ? `${analysis.usage.model} · ${analysis.usage.prompt_tokens}+${analysis.usage.completion_tokens} tok`
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
                <CardBody className="pt-4">
                  {blocked ? (
                    <div className="space-y-2">
                      <p className="text-sm font-medium text-block-ink">
                        No analysis was produced.
                      </p>
                      <p className="text-sm leading-relaxed text-muted-foreground">
                        {analysis.blocked_reason}
                      </p>
                    </div>
                  ) : (
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
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
                </CardBody>
              </Card>

              <p className="text-[0.72rem] leading-snug text-muted-foreground">
                {analysis.image != null ? (
                  <>
                    Declared <span className="font-mono text-foreground">
                      {analysis.image.declared_mime}
                    </span>
                    ; the bytes sniffed as{' '}
                    <span className="font-mono text-foreground">
                      {analysis.image.sniffed_mime ?? 'unrecognised'}
                    </span>{' '}
                    ({bytes(analysis.image.byte_size)}, provenance{' '}
                    <span className="font-mono text-foreground">{analysis.image.provenance}</span>
                    ). The declared type is attacker-controlled and never trusted — hygiene
                    compares it against the magic bytes.{' '}
                  </>
                ) : null}
                Cost is the analysis call only, in the units the deployment bills (
                {analysis.usage.images} image
                {analysis.usage.images === 1 ? '' : 's'}, source{' '}
                <span className="font-mono text-foreground">{analysis.usage.cost_source}</span>).
              </p>
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
