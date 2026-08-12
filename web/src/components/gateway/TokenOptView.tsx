'use client'

import {
  ArrowRight,
  Coins,
  Cpu,
  Loader2,
  PiggyBank,
  Route,
  ShieldQuestion,
  Timer,
  WifiOff,
} from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { getGatewayOptimization } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { probeBackend, type ResolvedMode } from '@/lib/api/mode'
import type { GatewayOptimizationResponse } from '@/lib/api/platform'

/** The response the mock adds an honest `sample`/`note` label to (live omits both). */
type OptimizationData = GatewayOptimizationResponse & { sample?: boolean; note?: string }

/** Format a USD figure, tolerating a null/undefined (offline, unmetered) reading. */
function usd(value: number | null | undefined): string {
  return value == null ? '—' : `$${value.toFixed(2)}`
}

/** Format a 0–1 share as a whole percent, tolerating a null (unmetered) reading. */
function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}

/**
 * Token-optimization — the `aegis` gateway savings surface. It leads with the
 * measured savings vs the frontier baseline (cost saved + small-model share),
 * then a per-role usage breakdown, then the read-only routing table the operator
 * runs on (role→model map, fallback chains, timeout, baseline model).
 *
 * HONESTY: offline there are no metered calls, so the real savings figure is
 * zero/null. The mock fixture flags itself with `sample: true` + a `note`; when
 * that flag is present the hero carries a "sample" badge and an explicit note so
 * the illustrative numbers are never passed off as a live measurement.
 */
function TokenOptView(): ReactElement {
  // Live session token — a constant `null` would 401 on a reload and, being
  // constant in the dependency array, never retry once the session was restored.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const [data, setData] = useState<OptimizationData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    getGatewayOptimization(token)
      .then((d) => {
        if (alive) {
          setData(d)
          setError(null)
        }
      })
      .catch(() => {
        if (alive) setError('Could not load the gateway optimization. Is the backend running?')
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  const summary = data?.summary
  const config = data?.config
  const isSample = data?.sample === true
  const roleRows = summary ? Object.entries(summary.by_role) : []

  return (
    <div className="space-y-6">
      {/* Section header */}
      <div>
        <p className="eyebrow mb-1">role→model routing · savings vs frontier baseline</p>
        <h1 className="t-hero text-foreground">Token optimization</h1>
      </div>

      {error ? (
        <Card>
          <CardBody>
            <p className="py-8 text-center text-sm text-danger">{error}</p>
          </CardBody>
        </Card>
      ) : data == null || summary == null || config == null ? (
        <Card>
          <CardBody>
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading optimization…
            </div>
          </CardBody>
        </Card>
      ) : (
        <>
          {/* ── Savings hero ──────────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis.gateway"
              title="Savings"
              description={`Measured against the frontier baseline — ${config.baseline_model} (the “${config.baseline_role}” role).`}
              actions={
                isSample ? (
                  <Badge tone="risk" className="gap-1.5">
                    <ShieldQuestion className="size-3" />
                    sample
                  </Badge>
                ) : (
                  <Badge tone="ok" className="gap-1.5">
                    <Coins className="size-3" />
                    metered
                  </Badge>
                )
              }
            />
            <CardBody className="space-y-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <StatCard
                  label="Cost saved"
                  value={usd(summary.cost_saved_usd)}
                  icon={PiggyBank}
                  tone="ok"
                />
                <StatCard
                  label="Small-model share"
                  value={pct(summary.small_model_share)}
                  icon={Cpu}
                  tone="agent"
                />
                <StatCard
                  label="Actual cost"
                  value={usd(summary.total_cost_usd)}
                  icon={Coins}
                  tone="neutral"
                />
                <StatCard
                  label="Baseline cost"
                  value={usd(summary.baseline_cost_usd)}
                  icon={Coins}
                  tone="block"
                />
              </div>

              {isSample ? (
                <p className="flex items-start gap-2 rounded-xl border border-risk/40 bg-risk/10 px-3.5 py-2.5 text-[0.78rem] leading-snug text-risk-ink">
                  <ShieldQuestion className="mt-0.5 size-3.5 shrink-0" />
                  <span>
                    <span className="font-semibold">Sample savings.</span>{' '}
                    {data.note ??
                      'Real figures are metered from live gateway calls.'}{' '}
                    Offline there are no metered calls, so the real saved figure is $0 — these numbers
                    are illustrative only.
                  </span>
                </p>
              ) : (
                <p className="text-[0.78rem] leading-snug text-muted-foreground">
                  Metered over {summary.total_calls.toLocaleString()} calls, of which{' '}
                  {summary.small_calls.toLocaleString()} were routed to a small model.
                </p>
              )}
            </CardBody>
          </Card>

          {/* ── Per-role breakdown ────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis.gateway"
              title="Per-role usage"
              description="Calls, tokens and cost per routing role — and whether the role sits on a small model."
            />
            <CardBody>
              <div className="overflow-x-auto rounded-xl border border-border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-muted-foreground">
                      <th className="px-4 py-2 font-medium">role</th>
                      <th className="px-4 py-2 text-right font-medium">calls</th>
                      <th className="px-4 py-2 text-right font-medium">tokens</th>
                      <th className="px-4 py-2 text-right font-medium">cost</th>
                      <th className="px-4 py-2 text-right font-medium">model tier</th>
                    </tr>
                  </thead>
                  <tbody>
                    {roleRows.map(([role, r]) => {
                      const tokens = r.prompt_tokens + r.completion_tokens
                      return (
                        <tr key={role} className="border-b border-border last:border-0">
                          <td className="px-4 py-2 font-mono text-foreground">{role}</td>
                          <td className="tabular px-4 py-2 text-right font-mono text-foreground">
                            {r.calls.toLocaleString()}
                          </td>
                          <td className="tabular px-4 py-2 text-right font-mono text-muted-foreground">
                            {tokens.toLocaleString()}
                          </td>
                          <td className="tabular px-4 py-2 text-right font-mono text-foreground">
                            {usd(r.cost_usd)}
                          </td>
                          <td className="px-4 py-2 text-right">
                            <Badge tone={r.small_model ? 'ok' : 'neutral'}>
                              {r.small_model ? 'small' : 'frontier'}
                            </Badge>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </CardBody>
          </Card>

          {/* ── Routing config ────────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis.gateway"
              title="Routing config"
              description="The effective role→model map, fallback chains, and gateway limits — read-only for the operator."
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <Route className="size-3" />
                  read-only
                </Badge>
              }
            />
            <CardBody className="space-y-5">
              {/* Role → model map */}
              <div>
                <p className="eyebrow mb-2">role → model</p>
                <div className="overflow-x-auto rounded-xl border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground">
                        <th className="px-4 py-2 font-medium">role</th>
                        <th className="px-4 py-2 font-medium">model</th>
                        <th className="px-4 py-2 font-medium">fallback chain</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(config.routing).map(([role, model]) => {
                        const chain = config.fallbacks[role] ?? []
                        const isBaseline = role === config.baseline_role
                        return (
                          <tr key={role} className="border-b border-border last:border-0">
                            <td className="px-4 py-2 font-mono text-foreground">
                              <span className="inline-flex items-center gap-1.5">
                                {role}
                                {isBaseline ? (
                                  <Badge tone="block">baseline</Badge>
                                ) : null}
                              </span>
                            </td>
                            <td className="px-4 py-2 font-mono text-muted-foreground">{model}</td>
                            <td className="px-4 py-2">
                              {chain.length > 0 ? (
                                <span className="flex flex-wrap items-center gap-1 font-mono text-[0.72rem] text-muted-foreground">
                                  {chain.map((step, i) => (
                                    <span key={step} className="inline-flex items-center gap-1">
                                      {i > 0 ? (
                                        <ArrowRight className="size-3 text-muted-foreground/60" />
                                      ) : null}
                                      <span className="rounded-md bg-surface-2 px-1.5 py-0.5 text-foreground">
                                        {step}
                                      </span>
                                    </span>
                                  ))}
                                </span>
                              ) : (
                                <span className="text-muted-foreground">—</span>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Gateway limits + baseline */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <div className="flex flex-col gap-1 rounded-xl border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow inline-flex items-center gap-1.5">
                    <Timer className="size-3" /> timeout
                  </span>
                  <span className="t-title tabular text-[0.95rem] font-semibold text-foreground">
                    {config.timeout_seconds}s
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-xl border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow">max output tokens</span>
                  <span className="t-title tabular text-[0.95rem] font-semibold text-foreground">
                    {config.max_output_tokens.toLocaleString()}
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-xl border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow">baseline role</span>
                  <span className="t-title text-[0.95rem] font-semibold text-foreground">
                    {config.baseline_role}
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-xl border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow">savings baseline model</span>
                  <span className="t-title truncate font-mono text-[0.82rem] font-semibold text-foreground">
                    {config.baseline_model}
                  </span>
                </div>
              </div>
            </CardBody>
          </Card>
        </>
      )}
    </div>
  )
}

/**
 * Client entry for the Token-optimization section. Runs the boot probe once
 * (live-first, mock fallback) before mounting the view, so the fetch reads the
 * resolved mode — the offline demo seeds from the mock fixture and is labelled
 * with the honest banner. Mirrors `MLOpsMount` / `LLMOpsMount`.
 */
export function TokenOptMount(): ReactElement {
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
      <TokenOptView />
    </div>
  )
}
