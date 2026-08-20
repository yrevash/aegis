'use client'

import {
  ArrowRight,
  Coins,
  Cpu,
  PiggyBank,
  Route,
  Timer,
} from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { getGatewayOptimization } from '@/lib/api/client'
import { getModels, type ModelRow } from '@/lib/api/console'
import { useAuth } from '@/lib/auth/AuthContext'
import type { GatewayOptimizationResponse } from '@/lib/api/platform'

/**
 * Format a USD figure, or say plainly that nothing metered it.
 *
 * These three helpers returned an em dash. In a cost column an em dash cannot be
 * told apart from `$0.00`, which is the difference between "we did not measure
 * this" and "this was free" — DESIGN.md §1 asks for the absence to be stated in
 * the slot the figure would have occupied, and on a savings page that distinction
 * is the whole claim.
 */
function usd(value: number | null | undefined): string {
  return value == null ? 'not metered' : `$${value.toFixed(2)}`
}

/** Format a 0–1 share as a whole percent, tolerating a null (unmetered) reading. */
function pct(value: number | null | undefined): string {
  return value == null ? 'not metered' : `${Math.round(value * 100)}%`
}

/** What one billing unit of a role costs, in the unit that role is actually billed in.
 *
 * Not "per 1k tokens": `GET /models` carries the role's own `billing_unit`, and a
 * column headed per-1k-tokens would have been quietly wrong for the voice role (billed
 * per audio minute) and the vision role (billed per image). An absent row renders a
 * dash — the price is unknown, which is a different statement from free.
 */
function priceLabel(row: ModelRow | undefined): string {
  if (row == null) return 'price unknown'
  const unit =
    row.billing_unit === 'audio_minutes'
      ? '/min'
      : row.billing_unit === 'images'
        ? '/image'
        : '/1k in'
  const out =
    row.output_cost_usd_per_1k > 0 ? ` · $${row.output_cost_usd_per_1k.toFixed(4)}/1k out` : ''
  return `$${row.input_cost_usd.toFixed(4)}${unit}${out}`
}

/**
 * Token-optimization — the `aegis` gateway savings surface. It leads with the
 * measured savings vs the frontier baseline (cost saved + small-model share),
 * then a per-role usage breakdown, then the read-only routing table the operator
 * runs on (role→model map, fallback chains, timeout, baseline model).
 *
 * Every figure is metered from real gateway calls. With no calls yet the savings
 * read `$0` / `—`, which is the truthful answer — there is no illustrative mode.
 */
function TokenOptView(): ReactElement {
  // Live session token — a constant `null` would 401 on a reload and, being
  // constant in the dependency array, never retry once the session was restored.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const [data, setData] = useState<GatewayOptimizationResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  // `GET /models` is the priced view of the same routing table this screen already
  // shows unpriced. Loaded separately and allowed to fail: the routing map is the
  // page, and a missing price renders as a dash rather than taking the page down.
  const [priced, setPriced] = useState<Map<string, ModelRow>>(new Map())

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
    getModels(token)
      .then((m) => {
        if (alive) setPriced(new Map(m.rows.map((row) => [row.role, row])))
      })
      .catch(() => {
        if (alive) setPriced(new Map())
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  const summary = data?.summary
  const config = data?.config
  const roleRows = summary ? Object.entries(summary.by_role) : []

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="role→model routing · savings vs frontier baseline"
        title="Token optimization"
        actions={
          <InfoTip label="Where these figures come from">
            Every figure is metered from real gateway calls. With no calls yet the
            savings read as unmetered rather than as zero — there is no illustrative
            mode.
          </InfoTip>
        }
      />

      {error ? (
        <ErrorState error={error} />
      ) : data == null || summary == null || config == null ? (
        <Card>
          <CardBody>
            <LoadingState rows={4} label="Reading the gateway meter…" />
          </CardBody>
        </Card>
      ) : (
        <>
          {/* ── Savings hero ──────────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis.gateway"
              title="Savings"
              actions={
                <Badge tone="ok" className="gap-1.5">
                  <Coins className="size-3" aria-hidden />
                  metered
                </Badge>
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

              <Receipt
                origin={`aegis.gateway · baseline ${config.baseline_model} (the “${config.baseline_role}” role)`}
                detail={`metered over ${summary.total_calls.toLocaleString()} calls, of which ${summary.small_calls.toLocaleString()} were routed to a small model`}
              />
            </CardBody>
          </Card>

          {/* ── Per-role breakdown ────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis.gateway"
              title="Per-role usage"
            />
            <CardBody>
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-muted-foreground">
                      <th scope="col" className="eyebrow px-4 py-2.5 font-medium">role</th>
                      <th scope="col" className="eyebrow px-4 py-2.5 text-right font-medium">calls</th>
                      <th scope="col" className="eyebrow px-4 py-2.5 text-right font-medium">tokens</th>
                      <th scope="col" className="eyebrow px-4 py-2.5 text-right font-medium">cost</th>
                      <th scope="col" className="eyebrow px-4 py-2.5 text-right font-medium">model tier</th>
                    </tr>
                  </thead>
                  <tbody>
                    {roleRows.map(([role, r]) => {
                      const tokens = r.prompt_tokens + r.completion_tokens
                      return (
                        <tr key={role} className="border-b border-border last:border-0">
                          <td className="px-4 py-2.5">
                            <Figure className="text-foreground">{role}</Figure>
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            <Figure className="text-foreground">{r.calls.toLocaleString()}</Figure>
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            <Figure className="text-muted-foreground">
                              {tokens.toLocaleString()}
                            </Figure>
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            <Figure className="text-foreground">{usd(r.cost_usd)}</Figure>
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
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <Route className="size-3" aria-hidden />
                  read-only
                </Badge>
              }
            />
            <CardBody className="space-y-5">
              {/* Role → model map */}
              <div>
                <p className="eyebrow mb-2">role → model</p>
                <div className="overflow-x-auto rounded-lg border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground">
                        <th scope="col" className="eyebrow px-4 py-2.5 font-medium">role</th>
                        <th scope="col" className="eyebrow px-4 py-2.5 font-medium">model</th>
                        <th scope="col" className="eyebrow px-4 py-2.5 font-medium">unit cost</th>
                        <th scope="col" className="eyebrow px-4 py-2.5 font-medium">fallback chain</th>
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
                            <td className="px-4 py-2 font-mono text-[0.72rem] text-muted-foreground">
                              {priceLabel(priced.get(role))}
                            </td>
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
                                <span className="text-xs text-muted-foreground italic">
                                  no fallback configured
                                </span>
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
                <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow inline-flex items-center gap-1.5">
                    <Timer className="size-3" aria-hidden /> timeout
                  </span>
                  <span className="tabular font-mono text-[0.95rem] font-semibold text-foreground">
                    {config.timeout_seconds}s
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow">max output tokens</span>
                  <span className="tabular font-mono text-[0.95rem] font-semibold text-foreground">
                    {config.max_output_tokens.toLocaleString()}
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow">baseline role</span>
                  <span className="text-[0.95rem] font-semibold text-foreground">
                    {config.baseline_role}
                  </span>
                </div>
                <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow">savings baseline model</span>
                  <span className="truncate font-mono text-[0.82rem] font-semibold text-foreground">
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

/** Client entry for the Token-optimization section — gated on a reachable backend. */
export function TokenOptMount(): ReactElement {
  return (
    <BackendGate>
      <TokenOptView />
    </BackendGate>
  )
}
