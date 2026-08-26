'use client'

import {
  ArrowDown,
  Ban,
  CircleSlash,
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
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { getSecurityPosture, runRedteam } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type {
  PostureEntry,
  RedteamReportResponse,
  SecurityPostureResponse,
} from '@/lib/api/platform'
import { BarChart } from '@/components/charts/BarChart'
import { RankedBars } from '@/components/charts/RankedBars'
import { SceneState } from '@/components/illustration/Scene'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Figure } from '@/components/primitives/Figure'
import { Gauge } from '@/components/primitives/Gauge'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { PageHeader } from '@/components/primitives/PageHeader'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { TenantRailPolicy } from '@/components/guardrails/TenantRailPolicy'
import { nemoState, postureAbsence } from '@/components/guardrail/postureState'
import { RailFiringLine } from '@/components/guardrail/RailFiringLine'
import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'

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
      'Fail-closed: a deterministic signature backstop runs first, and an unavailable classifier counts as injection.',
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

/**
 * The three coverage states a posture row can be in, each with the icon **and**
 * the word DESIGN.md §2 requires — `--ok` and `--risk` fail CVD separation
 * against each other by design, so the label is what actually tells them apart.
 */
const COVERAGE: { key: string; label: string; icon: LucideIcon; tone: 'ok' | 'risk' | 'neutral' }[] =
  [
    { key: 'enforced', label: 'enforced', icon: ShieldCheck, tone: 'ok' },
    { key: 'partial', label: 'partial', icon: ShieldAlert, tone: 'risk' },
    { key: 'not_covered', label: 'not covered', icon: CircleSlash, tone: 'neutral' },
  ]

/** Shorter axis labels for the rail layers a red-team verdict can name. */
const LAYER_AXIS_LABEL: Record<string, string> = {
  content_safety: 'content',
  injection_unavailable: 'inj · n/a',
}

/**
 * How many attacks each rail actually caught — the one distribution in this
 * payload that nothing rendered.
 *
 * `layer` is the rail that stamped the verdict, so a `null` is not missing data:
 * it is an attack that reached the end of the pipeline with no rail firing. That
 * bar is named `none` and counted, because dropping it would turn a chart about
 * coverage into a chart about successes.
 *
 * Benign controls are excluded — a control blocked by a rail is a false positive,
 * which is a different measurement and has its own figure on the hero.
 */
function layerDistribution(
  report: RedteamReportResponse | null,
): { layer: string; attacks: number }[] {
  if (report == null || !Array.isArray(report.attacks)) return []
  const counts = new Map<string, number>()
  for (const attack of report.attacks) {
    if (attack.category === 'benign_control') continue
    const key =
      attack.layer == null ? 'none' : (LAYER_AXIS_LABEL[attack.layer] ?? attack.layer)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  return [...counts.entries()]
    .map(([layer, attacks]) => ({ layer, attacks }))
    .sort((a, b) => b.attacks - a.attacks)
}

/** One measured figure beside the gauge — label above, value 4px below (§3). */
function HeroFigure({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-surface-2/40 p-3.5">
      <p className="eyebrow">{label}</p>
      <Figure size="stat" className="mt-1 block break-words text-foreground">
        {value}
      </Figure>
    </div>
  )
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
    <div className="relative rounded-lg border border-border bg-surface-2/40 p-4">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-card">
          <Icon className="size-5 text-muted-foreground" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Figure className="text-muted-foreground">
              {String(index + 1).padStart(2, '0')}
            </Figure>
            <h4 className="min-w-0 text-base font-semibold text-foreground">{spec.name}</h4>
            <Figure className="text-muted-foreground">{spec.layer}</Figure>
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
            <span className="flex min-w-0 items-center gap-1.5">
              <Figure className="min-w-0 break-words text-muted-foreground">{spec.fn}</Figure>
              <InfoTip label={`What the ${spec.name} rail checks`}>{spec.detail}</InfoTip>
            </span>
            <Badge tone="neutral">{spec.owasp}</Badge>
            <Badge tone={sem.tone} className="uppercase">
              <SemIcon className="size-3 shrink-0" aria-hidden />
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
      <SectionHeader as="h3" eyebrow={eyebrow} title={title} className="mb-3" />
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

/**
 * Engine indicator — programmatic pipeline vs NeMo Colang, both over one rail set.
 *
 * The Colang badge has **three** states, not two. It read `signals?.nemo_available ??
 * false`, and `signals` is null for every tenant-pinned principal — `/security/posture`
 * is platform-only and refuses them — so the fallback turned *"you are not allowed to
 * read this"* into the flat assertion **NOT INSTALLED**, about a package that is
 * installed. Two accounts on one deployment disagreed about a fact of the deployment.
 *
 * Unknown is not false. It gets its own word, the neutral tone DESIGN.md §4 reserves
 * for a state this build cannot classify, and the same sentence {@link PostureCoverage}
 * already prints twelve lines up.
 */
function EngineIndicator({
  signals,
  platformWide,
}: {
  signals: SecurityPostureResponse['signals'] | null
  /** Whether this principal may read process-wide facts at all (no tenant pin). */
  platformWide: boolean
}): ReactElement {
  const nemo = nemoState(signals)
  const absence = postureAbsence(platformWide)
  return (
    <Card className="rounded-lg">
      <CardBody>
        <SectionHeader
          as="h2"
          eyebrow="one rail set · two front doors"
          title="Guardrail engine"
          right={
            <InfoTip label="About the guardrail engine">
              One rail set, two front doors; the active-engine switch is a server setting
              posture does not surface, so the programmatic default is shown as active.
            </InfoTip>
          }
        />
        <div className="mt-4 grid gap-3 @container sm:grid-cols-2">
          <div className="min-w-0 rounded-lg border border-border bg-surface-2/40 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="min-w-0 font-medium text-foreground">Programmatic pipeline</span>
              <Badge tone="ok" className="uppercase">
                active
              </Badge>
            </div>
            <Figure className="mt-1.5 block break-words text-muted-foreground">
              guardrails.pipeline
            </Figure>
          </div>
          <div className="min-w-0 rounded-lg border border-border bg-surface-2/40 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="min-w-0 font-medium text-foreground">NeMo Colang</span>
              <Badge tone={nemo === 'available' ? 'ok' : 'neutral'} className="uppercase">
                {nemo}
              </Badge>
            </div>
            <p className="mt-1.5 flex items-center gap-1.5">
              <Figure className="min-w-0 break-words text-muted-foreground">
                guardrails_engine
              </Figure>
              <InfoTip label="About the NeMo Colang engine">
                Colang flows delegate to the same <code className="font-mono">check_input</code> /{' '}
                <code className="font-mono">check_output</code>.
              </InfoTip>
            </p>
            {nemo === 'unknown' ? (
              <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{absence.why}</p>
            ) : null}
          </div>
        </div>
      </CardBody>
    </Card>
  )
}

/**
 * The red-team hero — one gauge and four measured figures.
 *
 * `overall.blockRate` is a single value whose position inside a bounded range is
 * the whole point, which is the one job DESIGN.md §2 keeps a radial gauge for —
 * and it is one gauge, not a row of them. The floor it is judged against is
 * printed beside it rather than drawn on the arc: a target marker on a 148px ring
 * is a marker nobody can read off.
 */
function RedteamHero({
  report,
  loading,
  platformWide,
}: {
  report: RedteamReportResponse | null
  loading: boolean
  /** Whether this principal may read process-wide facts at all (no tenant pin). */
  platformWide: boolean
}): ReactElement {
  const overall = report?.overall ?? null

  return (
    <Card className="rounded-lg">
      <CardHeader
        eyebrow="POST /redteam/run · deterministic offline battery"
        title="Red-team block rate"
        actions={
          <div className="flex items-center gap-2">
            {report ? (
              <Badge tone={report.passed ? 'ok' : 'block'} className="gap-1.5 uppercase">
                {report.passed ? (
                  <ShieldCheck className="size-3 shrink-0" aria-hidden />
                ) : (
                  <Ban className="size-3 shrink-0" aria-hidden />
                )}
                {report.passed ? 'gate passed' : 'gate failed'}
              </Badge>
            ) : null}
            <InfoTip label="About the red-team block-rate">
              A leaked attack is a model-layer case the offline battery cannot judge without
              the live classifier.
            </InfoTip>
          </div>
        }
      />
      <CardBody className="@container space-y-4">
        {loading ? (
          <LoadingState rows={4} label="Running the offline battery…" />
        ) : report == null || overall == null ? (
          /* The scene depicts evidence gathered against an adversary, which is
             exactly what did not arrive. It sits above words that say the same
             thing, so it is never the only thing saying it (DESIGN.md §7). */
          <SceneState name="redteam" size="md">
            <Absence
              className="text-left"
              figure="Block rate"
              why={
                platformWide
                  ? 'The offline battery did not answer this session.'
                  : 'The battery runs across every tenant, so it is a platform-operations reading.'
              }
              needed={
                platformWide
                  ? 'Run it from the Red-team dashboard.'
                  : 'A devops or platform-admin account.'
              }
            />
          </SceneState>
        ) : (
          <div className="grid min-w-0 gap-5 @md:grid-cols-[auto_minmax(0,1fr)] @md:items-center">
            <Gauge
              value={overall.blockRate}
              label="block rate"
              color="agent"
              size={148}
              className="mx-auto"
            />
            <div className="grid min-w-0 gap-3 @sm:grid-cols-2 @3xl:grid-cols-4">
              <HeroFigure
                label="blocked"
                value={`${overall.attacksBlocked}/${overall.attacksTotal}`}
              />
              <HeroFigure
                label="floor"
                value={`${Math.round(report.thresholds.minBlockRate * 100)}%`}
              />
              <HeroFigure
                label="false positives"
                value={`${overall.falsePositives}/${overall.controlsTotal}`}
              />
              <HeroFigure
                label="fp ceiling"
                value={`${Math.round(report.thresholds.maxFalsePositiveRate * 100)}%`}
              />
            </div>
          </div>
        )}
        {report ? (
          <Receipt
            label="Gate"
            origin={`≥ ${Math.round(report.thresholds.minBlockRate * 100)}% block · ≤ ${Math.round(report.thresholds.maxFalsePositiveRate * 100)}% false-positive`}
          />
        ) : null}
      </CardBody>
    </Card>
  )
}

/**
 * Two charts over the same report: how well each attack family was held, and
 * which rail did the holding.
 *
 * Neither is a time-series, because there is no `ts` anywhere in this payload —
 * a red-team report is a snapshot, and the honest marks for a snapshot are
 * comparison and distribution.
 */
function RedteamCharts({ report }: { report: RedteamReportResponse }): ReactElement {
  const attackCategories = useMemo(
    () =>
      (Array.isArray(report.categories) ? report.categories : []).filter(
        (c) => c.category !== 'benign_control',
      ),
    [report.categories],
  )
  const byLayer = useMemo(() => layerDistribution(report), [report])
  const floor = report.thresholds.minBlockRate
  const below = attackCategories.filter((c) => c.blockRate < floor)
  const noRail = byLayer.find((row) => row.layer === 'none')?.attacks ?? 0

  return (
    <div className="grid min-w-0 gap-6 lg:grid-cols-2">
      <Card className="flex min-w-0 flex-col rounded-lg">
        <CardHeader
          as="h3"
          eyebrow="per attack family"
          title="Block rate by category"
          actions={
            <Badge tone="neutral" className="font-mono">
              {attackCategories.length} families
            </Badge>
          }
        />
        <CardBody className="flex min-h-0 flex-1 flex-col gap-4">
          {attackCategories.length === 0 ? (
            <Absence
              figure="Block rate by category"
              why="The battery reported only benign controls."
            />
          ) : (
            <>
              <RankedBars
                label="Block rate by attack category"
                data={attackCategories.map((c) => ({
                  name: c.category.replace(/_/g, ' '),
                  value: Math.round(c.blockRate * 100),
                }))}
                valueFormatter={(v) => `${v}%`}
                color="graph"
                maxRows={10}
                // A block rate is a rate: eleven of them do not add up to a
                // twelfth, so the tail is named as dropped rather than summed.
                tail="omit"
              />
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                {below.length === 0 ? (
                  <Badge tone="ok" className="gap-1.5">
                    <ShieldCheck className="size-3 shrink-0" aria-hidden />
                    every family clears the {Math.round(floor * 100)}% floor
                  </Badge>
                ) : (
                  below.map((c) => (
                    <Badge key={c.category} tone="block" className="gap-1.5">
                      <Ban className="size-3 shrink-0" aria-hidden />
                      {c.category.replace(/_/g, ' ')} below the floor
                    </Badge>
                  ))
                )}
              </div>
            </>
          )}
          <Receipt
            className="mt-auto"
            origin="redteam · categories[].blockRate"
            detail={`floor ${Math.round(floor * 100)}% · benign controls excluded`}
          />
        </CardBody>
      </Card>

      <Card className="flex min-w-0 flex-col rounded-lg">
        <CardHeader
          as="h3"
          eyebrow="attacks[].layer"
          title="Which rail caught the attack"
          actions={
            <Badge tone={noRail === 0 ? 'ok' : 'block'} className="gap-1.5">
              {noRail === 0 ? (
                <ShieldCheck className="size-3 shrink-0" aria-hidden />
              ) : (
                <Ban className="size-3 shrink-0" aria-hidden />
              )}
              {noRail === 0 ? 'every attack met a rail' : `${noRail} met no rail`}
            </Badge>
          }
        />
        <CardBody className="flex min-h-0 flex-1 flex-col gap-4">
          {byLayer.length === 0 ? (
            <Absence
              figure="Attacks per rail"
              why="No verdict in this battery named the rail that judged it."
            />
          ) : (
            <div className="min-w-0">
              <BarChart
                allowDecimals={false}
                data={byLayer}
                index="layer"
                category="attacks"
                color="graph"
                height={232}
                valueFormatter={(v) => `${v} attacks`}
              />
            </div>
          )}
          <Receipt
            className="mt-auto"
            origin="redteam · attacks[].layer"
            detail="`none` is an attack no rail fired on"
          />
        </CardBody>
      </Card>
    </div>
  )
}

/** The three-way coverage roll-up: icon, word and count for each posture state. */
function CoverageRollup({ entries }: { entries: PostureEntry[] }): ReactElement {
  const counts = new Map<string, number>()
  for (const entry of entries) counts.set(entry.status, (counts.get(entry.status) ?? 0) + 1)

  return (
    <div className="grid w-full gap-2.5 sm:grid-cols-3">
      {COVERAGE.map(({ key, label, icon: Icon, tone }) => (
        <div
          key={key}
          className="flex min-w-0 items-center gap-3 rounded-lg border border-border bg-surface-2/40 px-3.5 py-2.5"
        >
          <span
            className={cn(
              'grid size-9 shrink-0 place-items-center rounded-md',
              SIGNALS[tone].bg,
              SIGNALS[tone].text,
            )}
          >
            <Icon className="size-4" aria-hidden />
          </span>
          <span className="min-w-0">
            <Figure size="stat" className="block text-foreground">
              {String(counts.get(key) ?? 0)}
            </Figure>
            <span className="eyebrow">{label}</span>
          </span>
        </div>
      ))}
    </div>
  )
}

/**
 * OWASP coverage — every threat row the platform maps a control to, its live
 * status, and the introspected signals those statuses derive from.
 *
 * `GET /security/posture` was already being fetched for the rail badges; only two
 * of its rows (LLM01, LLM02) reached the screen, and its numeric signals reached
 * it not at all.
 */
function PostureCoverage({
  posture,
  platformWide,
}: {
  posture: SecurityPostureResponse | null
  /** Whether this principal may read process-wide facts at all (no tenant pin). */
  platformWide: boolean
}): ReactElement {
  const entries = posture?.entries ?? []
  const signals = posture?.signals ?? null

  return (
    <DataPanel
      eyebrow="GET /security/posture"
      title="OWASP coverage"
      maxHeight={440}
      actions={
        <InfoTip label="How coverage status is decided">
          Every status is derived on the server from the wiring signals below, not claimed
          here.
        </InfoTip>
      }
      toolbar={entries.length === 0 ? undefined : <CoverageRollup entries={entries} />}
      footer={
        signals == null ? undefined : (
          <Receipt
            label="Signals"
            origin={`${signals.hazard_categories} hazard categories · ${signals.rls_tables} RLS-enforced tables · max ${signals.max_plan_iterations} plan iterations`}
            detail={`pii engine ${signals.pii_engine} · mode ${signals.mode} · rls ${signals.rls_fail_closed ? 'fail-closed' : 'fail-open'}`}
          />
        )
      }
    >
      {entries.length === 0 ? (
        /* "Data held behind a lock" — the posture endpoint is the lock, and it
           did not open. The words below carry the state; the picture is silent. */
        <SceneState name="security" size="md">
          <Absence
            className="text-left"
            figure="OWASP coverage"
            why={postureAbsence(platformWide).why}
            needed={postureAbsence(platformWide).needed}
          />
        </SceneState>
      ) : (
        <Table className="min-w-[720px]">
          <THead>
            <TH className="w-28">Threat</TH>
            <TH>Control</TH>
            <TH className="w-40">Module</TH>
            <TH className="w-32">Status</TH>
          </THead>
          <TBody>
            {entries.map((entry) => {
              const status = statusBadge(entry.status)
              return (
                <TR key={entry.threat_id} className="align-top">
                  <TD>
                    <Figure className="text-foreground">{entry.threat_id}</Figure>
                    <p className="mt-1 text-xs text-muted-foreground">{entry.name}</p>
                  </TD>
                  <TD>
                    <span className="flex items-start gap-1.5">
                      <span className="min-w-0 text-sm leading-relaxed break-words text-foreground">
                        {entry.control}
                      </span>
                      <InfoTip label={`How ${entry.threat_id} is controlled`}>
                        {entry.mechanism}
                      </InfoTip>
                    </span>
                    {entry.refs.length > 0 ? (
                      <Figure className="mt-1 block break-words text-muted-foreground">
                        {entry.refs.join(' · ')}
                      </Figure>
                    ) : null}
                  </TD>
                  <TD>
                    <Figure className="break-words text-muted-foreground">{entry.module}</Figure>
                  </TD>
                  <TD>
                    <Badge tone={status.tone} className="uppercase">
                      {status.label}
                    </Badge>
                  </TD>
                </TR>
              )
            })}
          </TBody>
        </Table>
      )}
    </DataPanel>
  )
}

/**
 * Guardrails (§ rails) — the defense-in-depth pipeline made visible.
 *
 * The screen now leads with what the rails actually *did*: a gauge on the
 * measured block rate, the per-family rates ranked against the gate's floor, and
 * the distribution of which rail caught each attack — the one fact in this
 * payload that used to be fetched and never drawn. Under it sit the OWASP
 * coverage rows, then the ordered input and output rails with their true method,
 * OWASP mapping and verdict semantics, the active engine, and the tenant's own
 * folded policy.
 *
 * Nothing here is a trend: `POST /redteam/run` and `GET /security/posture` carry
 * no timestamp on any row, so every mark is comparison, composition or a single
 * bounded value.
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

  // The deployment's security posture and the attack battery are process-wide facts —
  // one number over every tenant that shared the worker — so `require_infra_reader`
  // refuses a tenant-pinned principal outright, and rightly. Firing these anyway gave a
  // tenant's own analyst two panels that could only ever 403, which is the same defect
  // the Cache section had. The gate is the tenant pin, never the role name: an un-pinned
  // ai_team operator is platform staff and still sees both.
  const platformWide = session?.tenantId == null

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    if (!platformWide) {
      setRedteamLoading(false)
      return
    }
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
        /* honest: the hero shows its stated absence */
      })
      // `finally` always resolves the spinner — a failure shows the stated
      // absence rather than spinning forever.
      .finally(() => {
        if (alive) setRedteamLoading(false)
      })
    return () => {
      alive = false
    }
  }, [token, hydrated, platformWide])

  const postureByThreat = new Map<string, PostureEntry>(
    (posture?.entries ?? []).map((e) => [e.threat_id, e]),
  )

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="rails · verdicts"
        title="Guardrails"
        actions={
          <InfoTip label="How to read the rail stack">
            The rails in the order they run, each with the OWASP row it answers.
          </InfoTip>
        }
      />

      {/* The rails, doing it — real payloads from the stored battery against the real
          input rail, one at a time. It leads because every panel below it describes the
          pipeline, and this one runs it. `INPUT_RAILS` is passed rather than re-declared
          so a verdict's `layer` is named by the same rail card the reader scrolls to. */}
      <RailFiringLine rails={INPUT_RAILS} />

      <RedteamHero report={redteam} loading={redteamLoading} platformWide={platformWide} />

      {redteam ? <RedteamCharts report={redteam} /> : null}

      <PostureCoverage posture={posture} platformWide={platformWide} />

      <div className="grid min-w-0 gap-6 xl:grid-cols-2">
        <Card className="min-w-0 rounded-lg">
          <CardBody className="min-w-0">
            <RailStack
              title="Input rails"
              eyebrow="on the request · before the model"
              rails={INPUT_RAILS}
              postureByThreat={postureByThreat}
            />
          </CardBody>
        </Card>
        <Card className="min-w-0 rounded-lg">
          <CardBody className="min-w-0">
            <RailStack
              title="Output rails"
              eyebrow="on the answer · before the user"
              rails={OUTPUT_RAILS}
              postureByThreat={postureByThreat}
            />
          </CardBody>
        </Card>
      </div>

      <EngineIndicator signals={posture?.signals ?? null} platformWide={platformWide} />

      <TenantRailPolicy />
    </div>
  )
}

/** Client entry for the Guardrails section — gated on a reachable backend. */
export function GuardrailsMount(): ReactElement {
  return (
    <BackendGate>
        <GuardrailsView />
    </BackendGate>
  )
}
