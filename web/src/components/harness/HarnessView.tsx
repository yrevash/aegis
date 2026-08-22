'use client'

import {
  CircleCheck,
  CircleDashed,
  Coins,
  Cpu,
  Hand,
  RefreshCcw,
  Timer,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { NodeGantt } from '@/components/charts/NodeGantt'
import { rampHex } from '@/components/charts/palette'
import { Scene, SceneState } from '@/components/illustration/Scene'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { BackendGate } from '@/components/shared/BackendGate'
import { QueryBar } from '@/components/console/QueryBar'
import { cn } from '@/lib/utils'
import { getHarnessConfig } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type { HarnessConfigResponse, HarnessKnob } from '@/lib/api/platform'
import { personasForRole } from '@/config/personas'
import type { NodeFinished, Role } from '@/lib/stream'
import { useRunStream } from '@/state/useRunStream'
import type { RunState } from '@/state/runReducer'

// ── Config formatting ─────────────────────────────────────────────────────────

/** Render a knob's effective / default value in a stable, honest way. */
function fmtValue(value: unknown): string {
  if (value === null || value === undefined) return 'none'
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  if (Array.isArray(value)) return value.map((v) => fmtValue(v)).join(' · ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** Render a knob's constraint column (allowed enum · numeric bound · free). */
function fmtConstraint(knob: HarnessKnob): string {
  if (knob.allowed && knob.allowed.length > 0) return knob.allowed.map(String).join(' · ')
  const bounds: string[] = []
  if (typeof knob.minimum === 'number') bounds.push(`≥ ${knob.minimum}`)
  if (typeof knob.maximum === 'number') bounds.push(`≤ ${knob.maximum}`)
  if (knob.nullable) bounds.push('nullable')
  return bounds.length > 0 ? bounds.join(' · ') : 'free'
}

/** Tone for the small type pill next to each knob. */
const TYPE_TONE: Record<string, BadgeTone> = {
  bool: 'agent',
  int: 'graph',
  float: 'graph',
  enum: 'ml',
  str: 'neutral',
}

/** The most donut slices the ordinal ramp carries before an "Other" band. */
const SLICE_CEILING = 4

// ── Run-trace shape (derived from the run stream) ─────────────────────────────

interface TraceTool {
  tool: string | null
  risk: string | null
  ok: boolean | null
  summary: string | null
}

interface TraceGate {
  gated: boolean
  risk: string | null
  action: string | null
  rationale: string | null
  resolved: boolean
}

interface RunTrace {
  runId: string | null
  traceId: string | null
  status: string | null
  /** The reducer's ordered `node_finished` ledger, verbatim — the one real series. */
  ledger: NodeFinished[]
  gate: TraceGate
  tools: TraceTool[]
  iterations: number
  totals: {
    promptTokens: number
    completionTokens: number
    costUsd: number
    durationMs: number
    cacheHit: boolean
  }
}

/**
 * Fold the reduced {@link RunState} into the same structured record shape the
 * backend's `run_summary()` produces — the ordered node ledger (with timings /
 * tokens / cost), the gate decision + risk tier, the joined tool calls, the
 * self-repair iteration count, and the terminal totals. Because it reads the
 * events the stream already collected, it can never diverge from what streamed.
 */
function deriveTrace(state: RunState): RunTrace {
  const resultByCall = new Map(state.toolResults.map((r) => [r.call_id, r]))
  const tools: TraceTool[] = state.toolCalls.map((c) => {
    const r = resultByCall.get(c.call_id)
    return { tool: c.tool, risk: c.risk, ok: r?.ok ?? null, summary: r?.summary ?? null }
  })

  const approvalEv = state.events.find((e) => e.type === 'approval_required')
  const gate: TraceGate =
    approvalEv && approvalEv.type === 'approval_required'
      ? {
          gated: true,
          risk: approvalEv.risk,
          action: approvalEv.action,
          rationale: approvalEv.rationale,
          resolved: state.approval === null,
        }
      : { gated: false, risk: null, action: null, rationale: null, resolved: true }

  const durationMs = state.nodeLedger.reduce((s, n) => s + n.duration_ms, 0)

  return {
    runId: state.runId,
    traceId: state.traceId,
    status: state.finishedStatus ?? state.phase,
    ledger: state.nodeLedger,
    gate,
    tools,
    // The reducer already collects every bounded self-repair round in
    // `reflections`; re-filtering the raw event log for them was a second,
    // divergable count of the same thing.
    iterations: state.reflections.length,
    totals: {
      promptTokens: state.usage?.prompt_tokens ?? 0,
      completionTokens: state.usage?.completion_tokens ?? 0,
      costUsd: state.usage?.cost_usd ?? 0,
      durationMs,
      cacheHit: state.usage?.cache_hit ?? false,
    },
  }
}

// ── Small presentation helpers ────────────────────────────────────────────────

const RISK_TONE: Record<string, BadgeTone> = { low: 'ok', medium: 'risk', high: 'block' }

/**
 * Money and counts through `Intl`, not template strings.
 *
 * A run's cost is fractions of a cent, so the currency format carries four
 * decimals rather than the default two — `$0.00` on every row would read as free.
 * The explicit locale is deliberate: `toLocaleString()` with none resolves to the
 * *runtime's* locale, which is the server's during SSR and the browser's after
 * hydration, and a figure that differs between the two is a hydration mismatch.
 */
const USD = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
})

const COUNT = new Intl.NumberFormat('en-US')

function usd(value: number): string {
  return value === 0 ? '$0' : USD.format(value)
}

function ms(value: number | null): string {
  return value === null ? '—' : `${COUNT.format(value)} ms`
}

/** Tone for a terminal status string. */
function statusTone(status: string | null): BadgeTone {
  if (status === 'completed') return 'ok'
  if (status === 'blocked' || status === 'error') return 'block'
  // A person declining is a refusal, not a fault: `risk` tone, not `block`.
  if (status === 'rejected') return 'risk'
  if (status === 'awaiting_approval' || status === 'streaming') return 'risk'
  return 'neutral'
}

// ── Config panel ──────────────────────────────────────────────────────────────

/**
 * The config area: a picture of the knob surface, then every `AgentConfig` knob
 * with its effective value, default and bounds. Each knob's `doc` — the only
 * prose here — lives behind the row's ⓘ rather than in a column of its own.
 *
 * `config.effective` was fetched on every load and rendered nowhere. It is the
 * dict the graph actually reads, so the honest thing it can tell a reader is
 * whether every effective key has a knob describing it — a key with no
 * descriptor is a setting nobody on this screen can see the bounds of.
 */
function ConfigPanel({ config }: { config: HarnessConfigResponse }): ReactElement {
  const tuned = useMemo(
    () => config.knobs.filter((k) => fmtValue(k.value) !== fmtValue(k.default)),
    [config],
  )

  /** Knobs by declared type — a real distribution, derived, not asserted. */
  const byType: DonutDatum[] = useMemo(() => {
    const counts = new Map<string, number>()
    for (const k of config.knobs) counts.set(k.type, (counts.get(k.type) ?? 0) + 1)
    const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1])
    const head = sorted.length > SLICE_CEILING ? sorted.slice(0, SLICE_CEILING - 1) : sorted
    const tail = sorted.slice(head.length)
    const rows = [
      ...head.map(([name, value]) => ({ name, value })),
      ...(tail.length
        ? [{ name: `Other (${tail.length})`, value: tail.reduce((s, [, v]) => s + v, 0) }]
        : []),
    ]
    return rows.map((r, i) => ({
      name: r.name,
      value: r.value,
      color: 'graph' as const,
      hex: rampHex(i, rows.length),
    }))
  }, [config])

  const undescribed = useMemo(() => {
    const keys = new Set(config.knobs.map((k) => k.key))
    return Object.keys(config.effective).filter((k) => !keys.has(k))
  }, [config])

  return (
    <div className="space-y-4">
      <Card className="min-w-0">
        <CardHeader
          eyebrow="aegis.agent · harness_config()"
          title="The knob surface"
          actions={
            <Badge tone="neutral" className="gap-1.5">
              <Cpu className="size-3" aria-hidden />
              read-only
              <InfoTip label="Why this panel is read-only">
                Tuning is host-side — the{' '}
                <code className="font-mono" translate="no">
                  AgentConfig
                </code>{' '}
                passed to a run — so editing here would be cosmetic.
              </InfoTip>
            </Badge>
          }
        />
        <CardBody className="@container/knobs space-y-4">
          <div className="grid items-center gap-6 @[44rem]/knobs:grid-cols-[auto_minmax(0,15rem)_minmax(0,1fr)]">
            <Scene name="tuning" size="sm" className="shrink-0" />
            <div className="min-w-0">
              <DonutChart
                data={byType}
                centerLabel={String(config.knobs.length)}
                centerSub="knobs"
                valueFormatter={(v) => String(v)}
                height={168}
              />
            </div>
            <div className="grid min-w-0 grid-cols-2 gap-3">
              <div className="flex min-w-0 flex-col gap-1 rounded-md border border-border bg-surface-2/40 p-3.5">
                <span className="eyebrow">tuned off default</span>
                <Figure size="stat">
                  {tuned.length}
                  <span className="text-muted-foreground">/{config.knobs.length}</span>
                </Figure>
              </div>
              <div className="flex min-w-0 flex-col gap-1 rounded-md border border-border bg-surface-2/40 p-3.5">
                <span className="eyebrow inline-flex items-center gap-1.5">
                  effective keys
                  <InfoTip label="About effective keys">
                    The dict the running graph reads; a key with no knob has no published bounds.
                  </InfoTip>
                </span>
                <Figure size="stat">{Object.keys(config.effective).length}</Figure>
              </div>
            </div>
          </div>
          {undescribed.length ? (
            <EmptyState
              title={`${undescribed.length} effective key${undescribed.length === 1 ? '' : 's'} with no knob`}
              body={undescribed.join(', ')}
            />
          ) : null}
          <Receipt origin="harness_config()" />
        </CardBody>
      </Card>

      <DataPanel
        className="min-w-0"
        eyebrow="AgentConfig"
        title="Every knob"
        toolbar={
          tuned.length ? (
            <Badge tone="risk">
              <Figure className="text-xs leading-4">{tuned.length}</Figure> tuned
            </Badge>
          ) : (
            <Badge tone="neutral">all at default</Badge>
          )
        }
        maxHeight={420}
      >
        <Table className="min-w-[46rem]">
          <THead>
            <TH>knob</TH>
            <TH>type</TH>
            <TH>value</TH>
            <TH>default</TH>
            <TH>allowed / bounds</TH>
          </THead>
          <TBody>
            {config.knobs.map((knob) => {
              const changed = fmtValue(knob.value) !== fmtValue(knob.default)
              return (
                <TR key={knob.key}>
                  <TD className="font-mono text-[0.8rem] break-words text-foreground">
                    <span className="inline-flex items-center gap-1.5" translate="no">
                      {knob.key}
                      {knob.doc ? (
                        <InfoTip label={`What ${knob.key} does`}>{knob.doc}</InfoTip>
                      ) : null}
                    </span>
                  </TD>
                  <TD>
                    <Badge tone={TYPE_TONE[knob.type] ?? 'neutral'}>{knob.type}</Badge>
                  </TD>
                  <TD className="tabular font-mono break-words text-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      {fmtValue(knob.value)}
                      {changed ? <Badge tone="risk">tuned</Badge> : null}
                    </span>
                  </TD>
                  <TD className="tabular font-mono break-words text-muted-foreground">
                    {fmtValue(knob.default)}
                  </TD>
                  <TD className="font-mono text-[0.72rem] break-words text-muted-foreground">
                    {fmtConstraint(knob)}
                  </TD>
                </TR>
              )
            })}
          </TBody>
        </Table>
      </DataPanel>
    </div>
  )
}

// ── Run-trace panel ───────────────────────────────────────────────────────────

/** A labelled outcome tile in the totals strip. */
function TotalTile({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon
  label: string
  value: string
}): ReactElement {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
      <span className="eyebrow inline-flex items-center gap-1.5">
        <Icon className="size-3 shrink-0" aria-hidden /> {label}
      </span>
      <Figure className="min-w-0 text-[0.95rem] leading-5 font-semibold break-words">{value}</Figure>
    </div>
  )
}

/** The run-trace area: node timeline, gate, tools, iterations, outcome. */
function TracePanel({ trace }: { trace: RunTrace | null }): ReactElement {
  if (trace === null) {
    return (
      <Card className="min-w-0">
        <CardBody>
          <SceneState name="empty" size="md">
            <EmptyState
              title="No run yet"
              body="Ask the agent something above; its live trace folds into this record."
            />
          </SceneState>
        </CardBody>
      </Card>
    )
  }

  const { promptTokens, completionTokens } = trace.totals
  const tokenSplit: DonutDatum[] =
    promptTokens + completionTokens > 0
      ? [
          { name: 'prompt', value: promptTokens, color: 'graph', hex: rampHex(0, 2) },
          { name: 'completion', value: completionTokens, color: 'ml', hex: rampHex(1, 2) },
        ]
      : []

  return (
    <div className="space-y-4">
      {/* The one real series this screen has: the ordered node ledger. */}
      <DataPanel
        className="min-w-0"
        eyebrow="aegis.agent · run_summary()"
        title="Run trace"
        actions={<Badge tone={statusTone(trace.status)}>{trace.status ?? '—'}</Badge>}
        // A per-node timeline is naturally wide. `maxHeight` is what makes
        // DataPanel's scroll container focusable and announced, so the rows a
        // 390px viewport pushes off to the right are reachable from a keyboard.
        maxHeight={520}
        footer={
          <Receipt
            origin={trace.traceId ?? trace.runId ?? 'run not identified'}
            detail={`${trace.ledger.length} node${trace.ledger.length === 1 ? '' : 's'} · ${usd(trace.totals.costUsd)}`}
          />
        }
      >
        {trace.ledger.length ? (
          <div className="min-w-[26rem]">
            <NodeGantt nodes={trace.ledger} />
          </div>
        ) : (
          <Absence
            figure="Per-node cost and latency"
            why="No node has finished in this run yet."
            needed="A completed graph node."
          />
        )}
      </DataPanel>

      {/* Outcome + token split */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,18rem)]">
        <Card className="min-w-0">
          <CardHeader eyebrow="outcome" title="What the run cost" />
          <CardBody>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <TotalTile icon={Timer} label="duration" value={ms(trace.totals.durationMs)} />
              <TotalTile icon={Coins} label="cost" value={usd(trace.totals.costUsd)} />
              <TotalTile
                icon={trace.totals.cacheHit ? CircleCheck : CircleDashed}
                label="cache"
                value={trace.totals.cacheHit ? 'hit' : 'miss'}
              />
            </div>
          </CardBody>
        </Card>

        <Card className="min-w-0">
          <CardHeader eyebrow="usage" title="Prompt vs completion" />
          <CardBody>
            {tokenSplit.length ? (
              <DonutChart
                data={tokenSplit}
                centerLabel={COUNT.format(promptTokens + completionTokens)}
                centerSub="tokens"
                valueFormatter={(v) => COUNT.format(v)}
                height={160}
              />
            ) : (
              <Absence
                figure="Token split"
                why="This run reported no token usage."
                needed="An LLM call in the graph."
              />
            )}
          </CardBody>
        </Card>
      </div>

      {/* Gate + self-repair */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="min-w-0">
          <CardBody>
            <div className={cn('flex flex-wrap items-center justify-between gap-2', trace.gate.gated && 'mb-2')}>
              <span className="eyebrow inline-flex min-w-0 items-center gap-1.5">
                <Hand className="size-3.5 shrink-0" aria-hidden /> approval gate
                <InfoTip label="About the approval gate">
                  A run is gated when a tool call reaches the risk floor set by{' '}
                  <code className="font-mono" translate="no">
                    gate_min_risk
                  </code>
                  .
                </InfoTip>
              </span>
              {trace.gate.gated ? <Badge tone="block">gated</Badge> : <Badge tone="ok">no gate</Badge>}
            </div>
            {trace.gate.gated ? (
              <div className="space-y-1.5 text-sm">
                <p className="flex flex-wrap items-center gap-2 text-muted-foreground">
                  risk tier
                  <Badge tone={RISK_TONE[trace.gate.risk ?? ''] ?? 'neutral'} className="uppercase">
                    {trace.gate.risk ?? '—'}
                  </Badge>
                  <span className="text-muted-foreground">·</span>
                  {trace.gate.resolved ? (
                    <span className="inline-flex items-center gap-1 text-[color:var(--ok-ink)]">
                      <CircleCheck className="size-3.5" aria-hidden /> resolved
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-risk-ink">
                      <CircleDashed className="size-3.5" aria-hidden /> awaiting decision
                    </span>
                  )}
                </p>
                {trace.gate.action ? (
                  <p className="break-words text-foreground">
                    action{' '}
                    <code className="font-mono text-[0.8rem]" translate="no">
                      {trace.gate.action}
                    </code>
                  </p>
                ) : null}
                {trace.gate.rationale ? (
                  <p className="text-[0.82rem] leading-snug text-muted-foreground">
                    {trace.gate.rationale}
                  </p>
                ) : null}
              </div>
            ) : null}
          </CardBody>
        </Card>

        <Card className="min-w-0">
          <CardBody>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="eyebrow inline-flex min-w-0 items-center gap-1.5">
                <RefreshCcw className="size-3.5 shrink-0" aria-hidden /> self-repair
                <InfoTip label="About self-repair">
                  The bounded Reflexion loop re-plans after a failed action; zero means one linear
                  pass.
                </InfoTip>
              </span>
              <Badge tone={trace.iterations > 0 ? 'ml' : 'neutral'}>
                {trace.iterations} iteration{trace.iterations === 1 ? '' : 's'}
              </Badge>
            </div>
          </CardBody>
        </Card>
      </div>

      {/* Tool calls */}
      <Card className="min-w-0">
        <CardHeader eyebrow="joined calls + results" title="Tool calls" />
        <CardBody>
          {trace.tools.length === 0 ? (
            <EmptyState
              icon={Wrench}
              title="No tool was called"
              body="The agent answered from retrieval alone."
            />
          ) : (
            <ul className="space-y-2">
              {trace.tools.map((t, i) => (
                <li
                  key={`${t.tool}-${i}`}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface/40 px-3 py-2"
                >
                  <span className="flex min-w-0 flex-wrap items-center gap-2">
                    <code className="font-mono text-[0.8rem] break-words text-foreground" translate="no">
                      {t.tool ?? '—'}
                    </code>
                    <Badge tone={RISK_TONE[t.risk ?? ''] ?? 'neutral'} className="uppercase">
                      {t.risk ?? '—'}
                    </Badge>
                    {t.summary ? (
                      <span className="min-w-0 truncate text-[0.8rem] text-muted-foreground">
                        {t.summary}
                      </span>
                    ) : null}
                  </span>
                  <Badge tone={t.ok === null ? 'risk' : t.ok ? 'ok' : 'block'}>
                    {t.ok === null ? 'pending' : t.ok ? 'ok' : 'failed'}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  )
}

// ── View + Mount ──────────────────────────────────────────────────────────────

/**
 * Harness (§ agentic harness) — the graph made legible and (host-side) tunable.
 *
 * The screen leads with what you can act on and then with what came back: the
 * ordered node ledger drawn as a gantt, which is the **only** genuine series in
 * this payload and is ordered within one run rather than across runs. The config
 * surface follows — the knob distribution and `effective`, both derived from
 * `getHarnessConfig()` and neither of them asserted.
 */
function HarnessView({ role }: { role: Role }): ReactElement {
  // Live session token — a constant `null` would fetch (and stream runs) with no
  // bearer on a reload and, being constant in the dependency array, never retry.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const [config, setConfig] = useState<HarnessConfigResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    getHarnessConfig(token)
      .then((c) => {
        if (alive) {
          setConfig(c)
          setError(null)
        }
      })
      .catch(() => {
        if (alive) setError('Could not load the harness config. Is the backend running?')
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  const { state, running, start, reset } = useRunStream()
  const [personaId, setPersonaId] = useState<string>(() => personasForRole(role)[0]?.id ?? '')

  const hasRun = state.events.length > 0
  const trace: RunTrace | null = hasRun ? deriveTrace(state) : null

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="graph · view + tweak" title="Harness" />

      {/* The thing you act on. */}
      <Card className="min-w-0">
        <CardHeader eyebrow="drive a run" title="Trace a query" />
        <CardBody className="pt-0">
          <QueryBar
            role={role}
            personaId={personaId}
            onPersonaChange={setPersonaId}
            onRun={(query) => start(query, personaId, token)}
            onReset={reset}
            running={running}
          />
        </CardBody>
      </Card>

      {/* What came back. */}
      <TracePanel trace={trace} />

      {/* The surface it ran on. */}
      {error ? (
        <ErrorState error={error} />
      ) : config === null ? (
        <Card className="min-w-0">
          <CardBody>
            <LoadingState rows={4} label="Loading harness config…" />
          </CardBody>
        </Card>
      ) : (
        <ConfigPanel config={config} />
      )}
    </div>
  )
}

/** Client entry for the Harness section — gated on a reachable backend. */
export function HarnessMount({ role }: { role: Role }): ReactElement {
  return (
    <BackendGate>
      <HarnessView role={role} />
    </BackendGate>
  )
}
