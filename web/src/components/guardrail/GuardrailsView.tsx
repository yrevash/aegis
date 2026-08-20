'use client'

import {
  ArrowDown,
  Ban,
  Cpu,
  Crosshair,
  Eraser,
  FileCode2,
  Filter,
  Fingerprint,
  ScanSearch,
  ShieldAlert,
  ShieldCheck,
  Target,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { getSecurityPosture, runRedteam } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type {
  PostureEntry,
  RedteamReportResponse,
  SecurityPostureResponse,
} from '@/lib/api/platform'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { MiniMeter } from '@/components/memory/MiniMeter'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { TenantRailPolicy } from '@/components/guardrails/TenantRailPolicy'

/**
 * One rail in the defense-in-depth pipeline. Every field here is honest,
 * module-real config read straight from `aegis/src/aegis/guardrails/pipeline.py`
 * (the `check_input` / `check_output` methods) — it is NOT a measured metric.
 * `postureThreatId`, when set, is the `GET /security/posture` entry whose live
 * status this rail's badge derives from; rails with no OWASP posture row are
 * always-on parts of the pipeline and badged `wired` instead.
 */
interface RailSpec {
  id: string
  /** The `layer` string the rail streams on its `guardrail` verdict event. */
  layer: string
  name: string
  icon: LucideIcon
  /** The true callable the rail runs, from the Python module — stays on the card. */
  fn: string
  /** Why/how it checks — relocated behind the card's ⓘ (§ prose relocation). */
  detail: string
  /** OWASP-Agentic / policy mapping. */
  owasp: string
  /** Verdict semantics the rail can emit. */
  semantics: 'block' | 'redact' | 'block / flag'
  /** Posture threat id whose live status this rail derives from, if any. */
  postureThreatId?: string
}

/** Input rails, in the exact order `Guardrails.check_input` runs them. */
const INPUT_RAILS: RailSpec[] = [
  {
    id: 'in-schema',
    layer: 'schema',
    name: 'Schema / format',
    icon: FileCode2,
    fn: 'validate_input_format',
    detail: 'The request parses and matches the expected shape.',
    owasp: 'LLM05 · improper input handling',
    semantics: 'block',
  },
  {
    id: 'in-pii',
    layer: 'pii',
    name: 'PII redaction (Presidio)',
    icon: Fingerprint,
    fn: 'pii.redact',
    detail:
      'Presidio plus anchored regex and a Luhn check mask PII before the model ever sees the request.',
    owasp: 'LLM02 · sensitive-information disclosure',
    semantics: 'redact',
    postureThreatId: 'LLM02',
  },
  {
    id: 'in-injection',
    layer: 'injection',
    name: 'Prompt injection',
    icon: ShieldAlert,
    fn: 'deterministic_injection → classify_injection',
    detail:
      'A deterministic signature backstop runs before the classifier, and the rail is fail-closed: an unavailable classifier is treated as injection.',
    owasp: 'LLM01 · prompt injection / jailbreak',
    semantics: 'block',
    postureThreatId: 'LLM01',
  },
  {
    id: 'in-content',
    layer: 'content_safety',
    name: 'Content safety',
    icon: ScanSearch,
    fn: 'screen_content',
    detail: 'Screens the request against the MLCommons hazard taxonomy.',
    owasp: 'Content policy · MLCommons S1–S13',
    semantics: 'block',
  },
  {
    id: 'in-topical',
    layer: 'topical',
    name: 'Topical scope',
    icon: Target,
    fn: 'screen_topic',
    detail: 'Off-domain and off-policy requests are screened out here.',
    owasp: 'LLM06 · excessive agency (scope)',
    semantics: 'block / flag',
  },
]

/** Output rails, in the exact order `Guardrails.check_output` runs them. */
const OUTPUT_RAILS: RailSpec[] = [
  {
    id: 'out-schema',
    layer: 'schema',
    name: 'Schema / format',
    icon: FileCode2,
    fn: 'validate_output_format',
    detail: 'The answer matches the expected output contract.',
    owasp: 'LLM05 · improper output handling',
    semantics: 'block',
  },
  {
    id: 'out-content-filter',
    layer: 'content',
    name: 'Content filter',
    icon: Filter,
    fn: 'schema.content_filter',
    detail: 'A deterministic banned-content filter over the draft answer.',
    owasp: 'Content policy',
    semantics: 'block',
  },
  {
    id: 'out-content',
    layer: 'content_safety',
    name: 'Content safety',
    icon: ScanSearch,
    fn: 'screen_content',
    detail: 'The same hazard taxonomy, re-run on the answer rather than the request.',
    owasp: 'Content policy · MLCommons S1–S13',
    semantics: 'block',
  },
  {
    id: 'out-grounding',
    layer: 'grounding',
    name: 'Grounding',
    icon: ShieldCheck,
    fn: 'check_grounding',
    detail:
      'The answer must be supported by the retrieved context — this is the anti-hallucination rail.',
    owasp: 'LLM09 · misinformation',
    semantics: 'block / flag',
  },
  {
    id: 'out-pii',
    layer: 'pii',
    name: 'PII redaction (Presidio)',
    icon: Fingerprint,
    fn: 'pii.redact',
    detail: 'Masks any PII left in the answer before it reaches the user.',
    owasp: 'LLM02 · sensitive-information disclosure',
    semantics: 'redact',
    postureThreatId: 'LLM02',
  },
]

/** Verdict-semantics → badge tone + icon. */
const SEMANTICS_META: Record<
  RailSpec['semantics'],
  { tone: 'block' | 'risk' | 'neutral'; icon: LucideIcon }
> = {
  block: { tone: 'block', icon: Ban },
  redact: { tone: 'risk', icon: Eraser },
  'block / flag': { tone: 'neutral', icon: ShieldAlert },
}

/** Posture status → badge tone + label. */
function statusBadge(status: string): { tone: 'ok' | 'risk' | 'neutral'; label: string } {
  if (status === 'enforced') return { tone: 'ok', label: 'enforced' }
  if (status === 'partial') return { tone: 'risk', label: 'partial' }
  if (status === 'not_covered') return { tone: 'neutral', label: 'not covered' }
  return { tone: 'neutral', label: status }
}

/** One rail card in the stepped stack. */
function RailCard({
  spec,
  index,
  postureByThreat,
}: {
  spec: RailSpec
  index: number
  postureByThreat: Map<string, PostureEntry>
}): ReactElement {
  const Icon = spec.icon
  const sem = SEMANTICS_META[spec.semantics]
  const SemIcon = sem.icon
  const posture = spec.postureThreatId ? postureByThreat.get(spec.postureThreatId) : undefined
  const status = posture ? statusBadge(posture.status) : null

  return (
    <div className="relative rounded-xl border border-border bg-surface-2/40 p-4">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl bg-card">
          <Icon className="size-5 text-muted-foreground" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[0.62rem] tabular-nums text-muted-foreground">
              {String(index + 1).padStart(2, '0')}
            </span>
            <h4 className="t-title text-foreground">{spec.name}</h4>
            <code className="font-mono text-[0.68rem] text-muted-foreground">{spec.layer}</code>
            {status ? (
              <Badge tone={status.tone} className="ml-auto uppercase">
                {status.label}
              </Badge>
            ) : (
              <Badge tone="neutral" className="ml-auto uppercase">
                wired
              </Badge>
            )}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <span className="flex items-center gap-1.5 font-mono text-[0.72rem] text-muted-foreground">
              {spec.fn}
              <InfoTip label={`What the ${spec.name} rail checks`}>{spec.detail}</InfoTip>
            </span>
            <Badge tone="graph">{spec.owasp}</Badge>
            <Badge tone={sem.tone} className="uppercase">
              <SemIcon className="size-3" />
              {spec.semantics}
            </Badge>
          </div>
        </div>
      </div>
    </div>
  )
}

/** A vertical stepped rail list (input or output), with connecting arrows. */
function RailStack({
  title,
  eyebrow,
  rails,
  postureByThreat,
}: {
  title: string
  eyebrow: string
  rails: RailSpec[]
  postureByThreat: Map<string, PostureEntry>
}): ReactElement {
  return (
    <div>
      <p className="eyebrow mb-1">{eyebrow}</p>
      <h3 className="t-title mb-3 text-foreground">{title}</h3>
      <ol className="space-y-2">
        {rails.map((spec, i) => (
          <li key={spec.id}>
            <RailCard spec={spec} index={i} postureByThreat={postureByThreat} />
            {i < rails.length - 1 && (
              <div className="flex justify-center py-1" aria-hidden="true">
                <ArrowDown className="size-4 text-muted-foreground/50" />
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

/** Engine indicator — programmatic pipeline vs NeMo Colang, both over one rail set. */
function EngineIndicator({
  signals,
}: {
  signals: SecurityPostureResponse['signals'] | null
}): ReactElement {
  const nemoAvailable = signals?.nemo_available ?? false
  return (
    <Card>
      <CardBody>
        <div className="flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-xl bg-blue-200/12">
            <Cpu className="size-5 text-blue-700" />
          </span>
          <div className="flex min-w-0 items-center gap-1.5">
            <h3 className="t-title text-foreground">Guardrail engine</h3>
            <InfoTip label="About the guardrail engine">
              One rail set, two front doors — the fast programmatic pipeline the agent graph calls,
              and the declarative NeMo Colang policy a reviewer reads. The tile below reads the
              posture <code className="font-mono">nemo_available</code> signal; the active-engine
              switch (<code className="font-mono">guardrails_engine</code>) is a server setting not
              surfaced in posture, so the programmatic default is shown as active.
            </InfoTip>
          </div>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-border bg-surface-2/40 p-4">
            <div className="flex items-center justify-between">
              <span className="font-medium text-foreground">Programmatic pipeline</span>
              <Badge tone="ok" className="uppercase">
                active
              </Badge>
            </div>
            <p className="mt-1.5 flex items-center gap-1.5 font-mono text-[0.72rem] text-muted-foreground">
              guardrails.pipeline
              <InfoTip label="About the programmatic pipeline">
                The default engine — it runs the rails in-process on every request.
              </InfoTip>
            </p>
          </div>
          <div className="rounded-xl border border-border bg-surface-2/40 p-4">
            <div className="flex items-center justify-between">
              <span className="font-medium text-foreground">NeMo Colang</span>
              <Badge tone={nemoAvailable ? 'graph' : 'neutral'} className="uppercase">
                {nemoAvailable ? 'available' : 'not installed'}
              </Badge>
            </div>
            <p className="mt-1.5 flex items-center gap-1.5 font-mono text-[0.72rem] text-muted-foreground">
              guardrails_engine
              <InfoTip label="About the NeMo Colang engine">
                Colang flows delegate to the same <code className="font-mono">check_input</code> /{' '}
                <code className="font-mono">check_output</code>; the engine is selected with the{' '}
                <code className="font-mono">guardrails_engine</code> setting.
              </InfoTip>
            </p>
          </div>
        </div>
      </CardBody>
    </Card>
  )
}

/** Red-team teaser — compact block-rate summary; the full report is its own dashboard. */
function RedteamTeaser({
  report,
  loading,
}: {
  report: RedteamReportResponse | null
  loading: boolean
}): ReactElement {
  // Attack categories only (drop the benign-control row, which measures false positives).
  const attackCategories = (report?.categories ?? []).filter(
    (c) => c.category !== 'benign_control',
  )
  const overall = report?.overall

  return (
    <Card>
      <CardBody>
        <div className="flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-xl bg-block/12">
            <Crosshair className="size-5 text-block-ink" />
          </span>
          <div className="flex min-w-0 flex-1 items-center gap-1.5">
            <h3 className="t-title text-foreground">Red-team block-rate</h3>
            <InfoTip label="About the red-team block-rate">
              A teaser from the deterministic offline attack battery — the full report is the
              Red-team dashboard. Leaked attacks are model-layer cases that need the live classifier.
            </InfoTip>
          </div>
        </div>

        {loading ? (
          <p className="mt-4 text-sm text-muted-foreground">Running the offline battery…</p>
        ) : !report || !overall ? (
          <div className="mt-4 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-4 text-center text-xs text-muted-foreground">
            Red-team battery unavailable — the full report lives on the Red-team dashboard.
          </div>
        ) : (
          <>
            <div className="mt-4 flex items-end gap-4">
              <div>
                <span className="tabular-nums text-3xl font-semibold text-foreground">
                  {Math.round(overall.blockRate * 100)}%
                </span>
                <p className="eyebrow mt-0.5">overall</p>
              </div>
              <div className="pb-1 text-sm text-muted-foreground">
                {overall.attacksBlocked}/{overall.attacksTotal} attacks blocked ·{' '}
                {Math.round(overall.falsePositiveRate * 100)}% false-positive on{' '}
                {overall.controlsTotal} benign controls
              </div>
              <Badge tone={report.passed ? 'ok' : 'block'} className="mb-1 ml-auto uppercase">
                {report.passed ? 'gate passed' : 'gate failed'}
              </Badge>
            </div>

            <div className="mt-4 space-y-2.5">
              {attackCategories.map((c) => (
                <div key={c.category} className="grid grid-cols-[10rem_1fr_3rem] items-center gap-3">
                  <span className="truncate text-sm text-foreground">
                    {c.category.replace(/_/g, ' ')}
                  </span>
                  <MiniMeter
                    value={c.blockRate}
                    hex={c.blockRate >= 0.75 ? 'var(--ok)' : 'var(--block)'}
                    height={8}
                  />
                  <span className="text-right font-mono text-[0.72rem] tabular-nums text-muted-foreground">
                    {Math.round(c.blockRate * 100)}%
                  </span>
                </div>
              ))}
            </div>

            <p className="mt-4 font-mono text-[0.68rem] text-muted-foreground">
              gate ≥ {Math.round(report.thresholds.minBlockRate * 100)}% block · ≤{' '}
              {Math.round(report.thresholds.maxFalsePositiveRate * 100)}% false-positive
            </p>
          </>
        )}
      </CardBody>
    </Card>
  )
}

/**
 * Guardrails (§ rails) — the defense-in-depth pipeline made visible: the ordered
 * input then output rails with their true method, OWASP mapping and verdict
 * semantics; the active engine (programmatic vs NeMo Colang); and a compact
 * red-team block-rate teaser. Rail statuses derive from `GET /security/posture`
 * where an OWASP row exists; the rest are always-on parts of the pipeline, badged
 * `wired`. Per-run guardrail verdicts belong to a run, so they are rendered on the
 * Console beside the run that produced them rather than mirrored here.
 */
function GuardrailsView(): ReactElement {
  // Live session token — the literal `null`s these two accessors were called with
  // sent no bearer, and an empty dependency array meant the calls never re-fired
  // once `AuthProvider` had restored the persisted session.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const [posture, setPosture] = useState<SecurityPostureResponse | null>(null)
  const [redteam, setRedteam] = useState<RedteamReportResponse | null>(null)
  const [redteamLoading, setRedteamLoading] = useState(true)

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    void getSecurityPosture(token)
      .then((p) => {
        if (alive) setPosture(p)
      })
      .catch(() => {
        /* honest: rail cards fall back to `wired` badges without posture */
      })
    void runRedteam(token)
      .then((r) => {
        if (alive) setRedteam(r)
      })
      .catch(() => {
        /* honest: teaser shows its unavailable empty state */
      })
      // `finally` always resolves the spinner — a failure shows the teaser's
      // empty state rather than spinning forever.
      .finally(() => {
        if (alive) setRedteamLoading(false)
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  const postureByThreat = new Map<string, PostureEntry>(
    (posture?.entries ?? []).map((e) => [e.threat_id, e]),
  )

  return (
    <div className="space-y-6">
      <div>
        <p className="eyebrow mb-1">rails · verdicts</p>
        <h1 className="t-hero text-foreground">Guardrails</h1>
      </div>

      <EngineIndicator signals={posture?.signals ?? null} />

      <TenantRailPolicy />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardBody>
            <RailStack
              title="Input rails"
              eyebrow="on the request · before the model"
              rails={INPUT_RAILS}
              postureByThreat={postureByThreat}
            />
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <RailStack
              title="Output rails"
              eyebrow="on the answer · before the user"
              rails={OUTPUT_RAILS}
              postureByThreat={postureByThreat}
            />
          </CardBody>
        </Card>
      </div>

      <RedteamTeaser report={redteam} loading={redteamLoading} />
    </div>
  )
}

/** Client entry for the Guardrails section — gated on a reachable backend. */
export function GuardrailsMount(): ReactElement {
  return (
    <BackendGate>
      <TooltipProvider>
        <GuardrailsView />
      </TooltipProvider>
    </BackendGate>
  )
}
