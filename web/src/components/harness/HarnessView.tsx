'use client'

import {
  CircleCheck,
  CircleDashed,
  Coins,
  Cpu,
  Hand,
  Loader2,
  Repeat,
  RefreshCcw,
  ShieldAlert,
  Sparkles,
  Timer,
  Waypoints,
  WifiOff,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { QueryBar } from '@/components/console/QueryBar'
import { getHarnessConfig } from '@/lib/api/client'
import { isMock, probeBackend, type ResolvedMode } from '@/lib/api/mode'
import type { HarnessConfigResponse, HarnessKnob } from '@/lib/api/platform'
import { personasForRole } from '@/config/personas'
import type { Role } from '@/lib/stream'
import { useRunStream } from '@/state/useRunStream'
import type { RunState } from '@/state/runReducer'

// ── Config formatting ─────────────────────────────────────────────────────────

/** Render a knob's effective / default value in a stable, honest way. */
function fmtValue(value: unknown): string {
  if (value === null || value === undefined) return 'none'
  if (typeof value === 'boolean') return value ? 'on' : 'off'
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

/** The four knobs surfaced as headline StatCards, with their icon + tone. */
const KEY_KNOBS: Array<{ key: string; icon: LucideIcon; tone: 'risk' | 'agent' | 'graph' | 'ml' }> = [
  { key: 'gate_min_risk', icon: ShieldAlert, tone: 'risk' },
  { key: 'max_plan_iterations', icon: Repeat, tone: 'agent' },
  { key: 'self_repair_enabled', icon: RefreshCcw, tone: 'ml' },
  { key: 'agentic_retrieval_max_rounds', icon: Waypoints, tone: 'graph' },
]

// ── Run-trace shape (derived from the run stream, or the offline sample) ───────

interface TraceNode {
  node: string
  label: string | null
  durationMs: number | null
  model: string | null
  promptTokens: number
  completionTokens: number
  costUsd: number
}

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
  nodes: TraceNode[]
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
  /** True when this is the illustrative offline sample, not a real run. */
  sample: boolean
}

/**
 * Fold the reduced {@link RunState} into the same structured record shape the
 * backend's `run_summary()` produces — the ordered node ledger (with timings /
 * tokens / cost), the gate decision + risk tier, the joined tool calls, the
 * self-repair iteration count, and the terminal totals. Because it reads the
 * events the stream already collected, it can never diverge from what streamed.
 */
function deriveTrace(state: RunState): RunTrace {
  const nodes: TraceNode[] = state.nodeLedger.map((n) => ({
    node: n.node,
    label: n.label,
    durationMs: n.duration_ms,
    model: n.model,
    promptTokens: n.prompt_tokens,
    completionTokens: n.completion_tokens,
    costUsd: n.cost_usd,
  }))

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

  // The wire may carry bounded self-repair `reflection` events (not in the
  // narrow client union) — count them defensively for the iteration readout.
  const iterations = state.events.filter((e) => (e.type as string) === 'reflection').length
  const durationMs = state.nodeLedger.reduce((s, n) => s + n.duration_ms, 0)

  return {
    runId: state.runId,
    traceId: state.traceId,
    status: state.finishedStatus ?? state.phase,
    nodes,
    gate,
    tools,
    iterations,
    totals: {
      promptTokens: state.usage?.prompt_tokens ?? 0,
      completionTokens: state.usage?.completion_tokens ?? 0,
      costUsd: state.usage?.cost_usd ?? 0,
      durationMs,
      cacheHit: state.usage?.cache_hit ?? false,
    },
    sample: false,
  }
}

/** The illustrative offline trace — the ops "gated refund" flow, clearly sampled. */
const MOCK_TRACE: RunTrace = {
  runId: 'run-mock-ops',
  traceId: 'trace-mock-ops',
  status: 'awaiting_approval',
  nodes: [
    { node: 'guard_input', label: 'Screening input', durationMs: 42, model: null, promptTokens: 0, completionTokens: 0, costUsd: 0 },
    { node: 'retrieve', label: 'Searching knowledge base', durationMs: 380, model: null, promptTokens: 0, completionTokens: 0, costUsd: 0 },
    { node: 'plan', label: 'Planning approach', durationMs: 910, model: 'gpt-4o-mini', promptTokens: 1120, completionTokens: 96, costUsd: 0.0009 },
    { node: 'ml', label: 'Scoring refund eligibility', durationMs: 120, model: null, promptTokens: 0, completionTokens: 0, costUsd: 0 },
    { node: 'act', label: 'Preparing action', durationMs: 210, model: 'gpt-4o-mini', promptTokens: 540, completionTokens: 82, costUsd: 0.0009 },
  ],
  gate: {
    gated: true,
    risk: 'high',
    action: 'issue_refund',
    rationale: 'Refund of $4,200 exceeds the $2,000 auto-approval ceiling — routing to the human gate.',
    resolved: false,
  },
  tools: [{ tool: 'issue_refund', risk: 'high', ok: null, summary: 'Awaiting approval' }],
  iterations: 0,
  totals: { promptTokens: 1660, completionTokens: 178, costUsd: 0.0018, durationMs: 1662, cacheHit: false },
  sample: true,
}

// ── Small presentation helpers ────────────────────────────────────────────────

const RISK_TONE: Record<string, BadgeTone> = { low: 'ok', medium: 'risk', high: 'block' }

function usd(value: number): string {
  return value === 0 ? '$0' : `$${value.toFixed(4)}`
}

function ms(value: number | null): string {
  return value === null ? '—' : `${value.toLocaleString()} ms`
}

/** Tone for a terminal status string. */
function statusTone(status: string | null): BadgeTone {
  if (status === 'completed') return 'ok'
  if (status === 'blocked' || status === 'error') return 'block'
  if (status === 'awaiting_approval' || status === 'streaming') return 'risk'
  return 'neutral'
}

// ── Config panel ──────────────────────────────────────────────────────────────

/** One headline StatCard for a key knob (falls back gracefully if absent). */
function KnobStat({
  knob,
  icon,
  tone,
}: {
  knob: HarnessKnob | undefined
  icon: LucideIcon
  tone: 'risk' | 'agent' | 'graph' | 'ml'
}): ReactElement | null {
  if (!knob) return null
  return (
    <StatCard label={knob.key} value={fmtValue(knob.value)} icon={icon} tone={tone} />
  )
}

/** The config (view + tweak) area: key StatCards + the full 11-knob table. */
function ConfigPanel({ config }: { config: HarnessConfigResponse }): ReactElement {
  const byKey = useMemo(
    () => new Map(config.knobs.map((k) => [k.key, k])),
    [config.knobs],
  )

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {KEY_KNOBS.map(({ key, icon, tone }) => (
          <KnobStat key={key} knob={byKey.get(key)} icon={icon} tone={tone} />
        ))}
      </div>

      <Card>
        <CardHeader
          eyebrow="aegis.agent · harness_config()"
          title="Tweakable configuration"
          description={`Every knob the graph actually reads — ${config.knobs.length} in all, each with its effective value, default and bounds.`}
          actions={
            <Badge tone="neutral" className="gap-1.5">
              <Cpu className="size-3" />
              read-only
            </Badge>
          }
        />
        <CardBody className="pt-0">
          <Table>
            <THead>
              <TH>knob</TH>
              <TH>type</TH>
              <TH>value</TH>
              <TH>default</TH>
              <TH>allowed / bounds</TH>
              <TH>what it does</TH>
            </THead>
            <TBody>
              {config.knobs.map((knob) => {
                const changed = fmtValue(knob.value) !== fmtValue(knob.default)
                return (
                  <TR key={knob.key}>
                    <TD className="font-mono text-[0.8rem] text-foreground">{knob.key}</TD>
                    <TD>
                      <Badge tone={TYPE_TONE[knob.type] ?? 'neutral'}>{knob.type}</Badge>
                    </TD>
                    <TD className="tabular font-mono text-foreground">
                      <span className="inline-flex items-center gap-1.5">
                        {fmtValue(knob.value)}
                        {changed ? <Badge tone="risk">tuned</Badge> : null}
                      </span>
                    </TD>
                    <TD className="tabular font-mono text-muted-foreground">{fmtValue(knob.default)}</TD>
                    <TD className="font-mono text-[0.72rem] text-muted-foreground">{fmtConstraint(knob)}</TD>
                    <TD className="max-w-md text-[0.8rem] leading-snug text-muted-foreground">{knob.doc}</TD>
                  </TR>
                )
              })}
            </TBody>
          </Table>
          <p className="mt-4 text-[0.78rem] leading-snug text-muted-foreground">
            Read-only by design: these are the effective values the running graph reads. Real tuning
            is host-side (the <code className="font-mono">AgentConfig</code> passed to a run), so a
            change here would be cosmetic — this panel keeps the harness honest, not editable.
          </p>
        </CardBody>
      </Card>
    </div>
  )
}

// ── Run-trace panel ───────────────────────────────────────────────────────────

/** A labelled outcome tile in the totals strip. */
function TotalTile({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }): ReactElement {
  return (
    <div className="flex flex-col gap-1 rounded-xl border border-border bg-surface-2/40 p-3.5">
      <span className="eyebrow inline-flex items-center gap-1.5">
        <Icon className="size-3" /> {label}
      </span>
      <span className="t-title tabular text-[0.95rem] font-semibold text-foreground">{value}</span>
    </div>
  )
}

/** The run-trace area: node timeline, gate, tools, iterations, outcome. */
function TracePanel({ trace }: { trace: RunTrace | null }): ReactElement {
  if (trace === null) {
    return (
      <Card>
        <CardBody>
          <div className="rounded-xl border border-dashed border-border bg-surface-2/30 px-4 py-10 text-center text-sm text-muted-foreground">
            No run yet — ask the agent something above to fold its live trace into this record.
          </div>
        </CardBody>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader
        eyebrow="aegis.agent · run_summary()"
        title="Run trace"
        description="The ordered node timeline, gate decision, tool calls and outcome — folded from the same event stream that drove the run."
        actions={
          trace.sample ? (
            <Badge tone="risk" className="gap-1.5">
              <Sparkles className="size-3" />
              sample
            </Badge>
          ) : (
            <Badge tone={statusTone(trace.status)}>{trace.status ?? '—'}</Badge>
          )
        }
      />
      <CardBody className="space-y-6 pt-0">
        {/* Node timeline */}
        <div>
          <p className="eyebrow mb-2">node timeline</p>
          <Table>
            <THead>
              <TH>#</TH>
              <TH>node</TH>
              <TH>step</TH>
              <TH>duration</TH>
              <TH>model</TH>
              <TH>tokens</TH>
              <TH>cost</TH>
            </THead>
            <TBody>
              {trace.nodes.map((n, i) => (
                <TR key={`${n.node}-${i}`}>
                  <TD className="tabular text-muted-foreground">{i + 1}</TD>
                  <TD className="font-mono text-[0.8rem] text-foreground">{n.node}</TD>
                  <TD className="text-[0.82rem] text-muted-foreground">{n.label ?? '—'}</TD>
                  <TD className="tabular font-mono text-foreground">{ms(n.durationMs)}</TD>
                  <TD className="font-mono text-[0.72rem] text-muted-foreground">{n.model ?? '—'}</TD>
                  <TD className="tabular font-mono text-muted-foreground">
                    {(n.promptTokens + n.completionTokens).toLocaleString()}
                  </TD>
                  <TD className="tabular font-mono text-foreground">{usd(n.costUsd)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>

        {/* Gate + iterations */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-border bg-surface-2/40 p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="eyebrow inline-flex items-center gap-1.5">
                <Hand className="size-3.5" /> approval gate
              </span>
              {trace.gate.gated ? (
                <Badge tone="block">gated</Badge>
              ) : (
                <Badge tone="ok">no gate</Badge>
              )}
            </div>
            {trace.gate.gated ? (
              <div className="space-y-1.5 text-sm">
                <p className="flex items-center gap-2 text-muted-foreground">
                  risk tier
                  <Badge tone={RISK_TONE[trace.gate.risk ?? ''] ?? 'neutral'} className="uppercase">
                    {trace.gate.risk ?? '—'}
                  </Badge>
                  <span className="text-muted-foreground">·</span>
                  {trace.gate.resolved ? (
                    <span className="inline-flex items-center gap-1 text-[color:var(--success)]">
                      <CircleCheck className="size-3.5" /> resolved
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-risk-ink">
                      <CircleDashed className="size-3.5" /> awaiting decision
                    </span>
                  )}
                </p>
                {trace.gate.action ? (
                  <p className="text-foreground">
                    action <code className="font-mono text-[0.8rem]">{trace.gate.action}</code>
                  </p>
                ) : null}
                {trace.gate.rationale ? (
                  <p className="text-[0.82rem] leading-snug text-muted-foreground">{trace.gate.rationale}</p>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No action reached the risk floor — the run completed without a human gate.
              </p>
            )}
          </div>

          <div className="rounded-xl border border-border bg-surface-2/40 p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="eyebrow inline-flex items-center gap-1.5">
                <RefreshCcw className="size-3.5" /> self-repair
              </span>
              <Badge tone={trace.iterations > 0 ? 'ml' : 'neutral'}>
                {trace.iterations} iteration{trace.iterations === 1 ? '' : 's'}
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              {trace.iterations > 0
                ? 'The bounded Reflexion loop reflected and re-planned after an insufficient action.'
                : 'A single linear pass — no reflect → re-plan cycle was needed.'}
            </p>
          </div>
        </div>

        {/* Tool calls */}
        <div>
          <p className="eyebrow mb-2 inline-flex items-center gap-1.5">
            <Wrench className="size-3.5" /> tool calls
          </p>
          {trace.tools.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border bg-surface-2/30 px-4 py-6 text-center text-sm text-muted-foreground">
              No tools were called — the agent answered from retrieval alone.
            </div>
          ) : (
            <ul className="space-y-2">
              {trace.tools.map((t, i) => (
                <li
                  key={`${t.tool}-${i}`}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface/40 px-3 py-2"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <code className="font-mono text-[0.8rem] text-foreground">{t.tool ?? '—'}</code>
                    <Badge tone={RISK_TONE[t.risk ?? ''] ?? 'neutral'} className="uppercase">
                      {t.risk ?? '—'}
                    </Badge>
                    {t.summary ? (
                      <span className="truncate text-[0.8rem] text-muted-foreground">{t.summary}</span>
                    ) : null}
                  </span>
                  <Badge tone={t.ok === null ? 'risk' : t.ok ? 'ok' : 'block'}>
                    {t.ok === null ? 'pending' : t.ok ? 'ok' : 'failed'}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Outcome totals */}
        <div>
          <p className="eyebrow mb-2">outcome</p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <TotalTile icon={Timer} label="duration" value={ms(trace.totals.durationMs)} />
            <TotalTile
              icon={Cpu}
              label="tokens"
              value={(trace.totals.promptTokens + trace.totals.completionTokens).toLocaleString()}
            />
            <TotalTile icon={Coins} label="cost" value={usd(trace.totals.costUsd)} />
            <TotalTile
              icon={trace.totals.cacheHit ? CircleCheck : CircleDashed}
              label="cache"
              value={trace.totals.cacheHit ? 'hit' : 'miss'}
            />
          </div>
        </div>
      </CardBody>
    </Card>
  )
}

// ── View + Mount ──────────────────────────────────────────────────────────────

/**
 * Harness (§ agentic harness) — the graph made legible and (host-side) tunable.
 * Two areas: the config panel renders EVERY `AgentConfig` knob from
 * `getHarnessConfig()` (value / default / bounds / doc), read-only and honest;
 * the run-trace panel folds a live run's event stream into the same record the
 * backend's `run_summary()` produces — node timeline, gate decision + risk tier,
 * tool calls, self-repair iterations and terminal totals. Offline it seeds the
 * trace from a clearly-labelled sample; a run replaces it with the real one.
 */
function HarnessView({ role, mock }: { role: Role; mock: boolean }): ReactElement {
  const token: string | null = null
  const [config, setConfig] = useState<HarnessConfigResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    getHarnessConfig(token)
      .then((c) => {
        if (alive) setConfig(c)
      })
      .catch(() => {
        if (alive) setError('Could not load the harness config. Is the backend running?')
      })
    return () => {
      alive = false
    }
  }, [token])

  const { state, running, start, reset } = useRunStream()
  const [personaId, setPersonaId] = useState<string>(() => personasForRole(role)[0]?.id ?? '')

  const hasRun = state.events.length > 0
  const trace: RunTrace | null = hasRun ? deriveTrace(state) : mock ? MOCK_TRACE : null

  return (
    <div className="space-y-6">
      <div>
        <p className="eyebrow mb-1">graph · view + tweak</p>
        <h1 className="t-hero text-foreground">Harness</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          How the agentic graph is wired and what you can tune — every knob it reads, then a live
          run folded into one glass-box record: the node timeline, the gate decision, the tools it
          fired and what it cost.
        </p>
      </div>

      {/* Config (view + tweak) */}
      {error ? (
        <Card>
          <CardBody>
            <p className="py-8 text-center text-sm text-danger">{error}</p>
          </CardBody>
        </Card>
      ) : config === null ? (
        <Card>
          <CardBody>
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading harness config…
            </div>
          </CardBody>
        </Card>
      ) : (
        <ConfigPanel config={config} />
      )}

      {/* Run trace */}
      <Card>
        <CardHeader
          eyebrow="drive a run"
          title="Trace a query"
          description="Run a query to fold its live event stream into the record below."
        />
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

      <TracePanel trace={trace} />
    </div>
  )
}

/**
 * Client entry for the Harness section. Runs the boot probe once (live-first,
 * mock fallback) before mounting the view, mirroring `TokenOptMount` /
 * `CacheMount`. Offline is labelled with the honest banner; the config panel
 * reads the mock knob set and the trace seeds from the sample until a run.
 */
export function HarnessMount({ role }: { role: Role }): ReactElement {
  const [mode, setMode] = useState<ResolvedMode | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setMode(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (mode === null) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <div>
      {mode.mode === 'mock' && (
        <div
          role="status"
          className="mb-4 flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
        >
          <WifiOff className="size-3.5 shrink-0" />
          <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
        </div>
      )}
      <HarnessView role={role} mock={isMock()} />
    </div>
  )
}
