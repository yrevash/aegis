'use client'

import {
  CircleCheck,
  CircleDashed,
  Coins,
  Cpu,
  Hand,
  Loader2,
  RefreshCcw,
  Timer,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { InfoTip } from '@/components/primitives/InfoTip'
import { BackendGate } from '@/components/shared/BackendGate'
import { QueryBar } from '@/components/console/QueryBar'
import { cn } from '@/lib/utils'
import { getHarnessConfig } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
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

// ── Run-trace shape (derived from the run stream) ─────────────────────────────

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
  }
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

/**
 * The config area: every `AgentConfig` knob the graph reads, with its effective
 * value, default and bounds. Each knob's `doc` — the only prose here — lives
 * behind the row's ⓘ rather than in a column of its own.
 */
function ConfigPanel({ config }: { config: HarnessConfigResponse }): ReactElement {
  return (
    <Card>
      <CardHeader
        eyebrow="aegis.agent · harness_config()"
        title={`Tweakable configuration · ${config.knobs.length} knobs`}
        actions={
          <Badge tone="neutral" className="gap-1.5">
            <Cpu className="size-3" aria-hidden />
            read-only
            <InfoTip label="Why this panel is read-only">
              These are the effective values the running graph reads. Real tuning is host-side (the{' '}
              <code className="font-mono">AgentConfig</code> passed to a run), so editing here would
              be cosmetic — the panel keeps the harness honest, not editable.
            </InfoTip>
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
          </THead>
          <TBody>
            {config.knobs.map((knob) => {
              const changed = fmtValue(knob.value) !== fmtValue(knob.default)
              return (
                <TR key={knob.key}>
                  <TD className="font-mono text-[0.8rem] text-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      {knob.key}
                      {knob.doc ? (
                        <InfoTip label={`What ${knob.key} does`}>{knob.doc}</InfoTip>
                      ) : null}
                    </span>
                  </TD>
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
                </TR>
              )
            })}
          </TBody>
        </Table>
      </CardBody>
    </Card>
  )
}

// ── Run-trace panel ───────────────────────────────────────────────────────────

/** A labelled outcome tile in the totals strip. */
function TotalTile({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }): ReactElement {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
      <span className="eyebrow inline-flex items-center gap-1.5">
        <Icon className="size-3" aria-hidden /> {label}
      </span>
      <Figure className="text-[0.95rem] leading-5 font-semibold">{value}</Figure>
    </div>
  )
}

/** The run-trace area: node timeline, gate, tools, iterations, outcome. */
function TracePanel({ trace }: { trace: RunTrace | null }): ReactElement {
  if (trace === null) {
    return (
      <Card>
        <CardBody>
          <div className="rounded-lg border border-dashed border-border bg-surface-2/30 px-4 py-10 text-center text-sm text-muted-foreground">
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
        actions={<Badge tone={statusTone(trace.status)}>{trace.status ?? '—'}</Badge>}
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
          <div className="rounded-lg border border-border bg-surface-2/40 p-4">
            <div className={cn('flex items-center justify-between', trace.gate.gated && 'mb-2')}>
              <span className="eyebrow inline-flex items-center gap-1.5">
                <Hand className="size-3.5" aria-hidden /> approval gate
                <InfoTip label="About the approval gate">
                  A run is gated when a tool call reaches the risk floor set by{' '}
                  <code className="font-mono">gate_min_risk</code>. &ldquo;No gate&rdquo; means
                  nothing in this run reached that floor, so it completed without a human.
                </InfoTip>
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
                  <p className="text-foreground">
                    action <code className="font-mono text-[0.8rem]">{trace.gate.action}</code>
                  </p>
                ) : null}
                {trace.gate.rationale ? (
                  <p className="text-[0.82rem] leading-snug text-muted-foreground">{trace.gate.rationale}</p>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className="rounded-lg border border-border bg-surface-2/40 p-4">
            <div className="flex items-center justify-between">
              <span className="eyebrow inline-flex items-center gap-1.5">
                <RefreshCcw className="size-3.5" aria-hidden /> self-repair
                <InfoTip label="About self-repair">
                  The bounded Reflexion loop reflects and re-plans after a failed or insufficient
                  action. Zero iterations means the run was a single linear pass.
                </InfoTip>
              </span>
              <Badge tone={trace.iterations > 0 ? 'ml' : 'neutral'}>
                {trace.iterations} iteration{trace.iterations === 1 ? '' : 's'}
              </Badge>
            </div>
          </div>
        </div>

        {/* Tool calls */}
        <div>
          <p className="eyebrow mb-2 inline-flex items-center gap-1.5">
            <Wrench className="size-3.5" aria-hidden /> tool calls
          </p>
          {trace.tools.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border bg-surface-2/30 px-4 py-6 text-center text-sm text-muted-foreground">
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
 * tool calls, self-repair iterations and terminal totals. Before a run there is
 * no trace to show, and the panel says so rather than seeding a stand-in.
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
            <div
              role="status"
              className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground"
            >
              <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden />
              Loading harness config…
            </div>
          </CardBody>
        </Card>
      ) : (
        <ConfigPanel config={config} />
      )}

      {/* Run trace */}
      <Card>
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

      <TracePanel trace={trace} />
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
