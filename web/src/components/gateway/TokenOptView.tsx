'use client'

import { ArrowRight, Coins, PiggyBank, Route, Timer } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { BarChart } from '@/components/charts/BarChart'
import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { RankedBars } from '@/components/charts/RankedBars'
import { rampHex } from '@/components/charts/palette'
import { SceneState } from '@/components/illustration/Scene'
import { Figure } from '@/components/primitives/Figure'
import { Gauge } from '@/components/primitives/Gauge'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { StatCard } from '@/components/ui/StatCard'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
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

/** A chart card that has nothing to draw because nothing was metered. */
function NotMetered({ figure }: { figure: string }): ReactElement {
  return <Absence figure={figure} why="No gateway call has been metered yet." />
}

/**
 * Token-optimization — the `aegis` gateway savings surface.
 *
 * There is no timestamp anywhere in `GET /gateway/optimization`, so nothing here
 * is a trend: the marks are one bounded value (small-model share), one comparison
 * (spend against the frontier baseline) and two compositions (spend and volume by
 * role, prompt against completion tokens). Every figure is metered from real
 * calls; with no calls the charts state their absence rather than drawing zeros.
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

  // Nothing has been metered until a call has been. A bar chart of zeros reads as
  // "measured, and it was zero", which is a different claim from "not measured".
  const metered = (summary?.total_calls ?? 0) > 0

  const promptTokens = roleRows.reduce((sum, [, r]) => sum + r.prompt_tokens, 0)
  const completionTokens = roleRows.reduce((sum, [, r]) => sum + r.completion_tokens, 0)
  const tokenSplit: DonutDatum[] = [
    { name: 'prompt', value: promptTokens },
    { name: 'completion', value: completionTokens },
  ]
    .sort((a, b) => b.value - a.value)
    .map((d, i) => ({ ...d, color: 'graph' as const, hex: rampHex(i, 2) }))

  return (
    <div className="min-w-0 space-y-6">
      <PageHeader
        eyebrow="role→model routing · savings vs frontier baseline"
        title="Token optimization"
        actions={
          <InfoTip label="Where these figures come from">
            Every figure is metered from real gateway calls; with none, the savings read as
            unmetered rather than as zero.
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
          {/* ── Savings hero — one bounded value, three measured totals ───────── */}
          <Card className="min-w-0">
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
            <CardBody className="@container space-y-4">
              <div className="grid min-w-0 gap-5 @lg:grid-cols-[auto_minmax(0,1fr)] @lg:items-center">
                {/*
                  The gauge under a card headed **Savings** is the savings rate, and
                  nothing else. It used to be `small_model_share`, which on this
                  deployment is a true `0` — every role routes to a model the price
                  table does not class as small — sitting one gutter away from
                  `Cost saved $0.62`. Two measured figures, both correct, rendered as
                  a flat contradiction: *savings, metered, 0%* beside 62 cents saved.

                  The saving is real and comes from somewhere else — each role's own
                  cheaper model against the baseline role's price — so the bounded
                  value the gauge exists for (DESIGN.md §2) is `cost_saved /
                  baseline_cost`. The small-model share keeps its place in the receipt
                  below, where "n calls, 0 on a small model" states it without
                  claiming to be the headline.

                  A baseline of zero is not a 0% saving, it is no denominator — so it
                  gets the stated absence rather than an arc at nought.
                */}
                {!metered || summary.baseline_cost_usd <= 0 ? (
                  <Absence
                    className="max-w-xs"
                    figure="Savings rate"
                    why={
                      metered
                        ? 'The baseline priced at zero, so there is no rate to take.'
                        : 'No call has been routed yet.'
                    }
                  />
                ) : (
                  <Gauge
                    value={summary.cost_saved_usd / summary.baseline_cost_usd}
                    label="saved vs baseline"
                    color="agent"
                    size={148}
                    className="mx-auto"
                  />
                )}
                <div className="grid min-w-0 gap-4 @sm:grid-cols-3">
                  <StatCard
                    label="Cost saved"
                    value={usd(summary.cost_saved_usd)}
                    icon={PiggyBank}
                    tone="ok"
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
              </div>

              <Receipt
                origin={`aegis.gateway · baseline ${config.baseline_model} (the “${config.baseline_role}” role)`}
                detail={`${summary.total_calls.toLocaleString()} calls, ${summary.small_calls.toLocaleString()} on a small model`}
              />
            </CardBody>
          </Card>

          {/* ── The composition marks ─────────────────────────────────────────── */}
          <div className="grid min-w-0 gap-6 lg:grid-cols-2">
            <Card className="flex min-w-0 flex-col">
              <CardHeader as="h3" eyebrow="summary" title="Spend against the baseline" />
              <CardBody className="flex min-h-0 flex-1 flex-col gap-4">
                {!metered ? (
                  <NotMetered figure="Spend against the baseline" />
                ) : (
                  <div className="min-w-0">
                    <BarChart
                      data={[
                        { scenario: 'baseline', usd: summary.baseline_cost_usd },
                        { scenario: 'actual', usd: summary.total_cost_usd },
                        { scenario: 'saved', usd: summary.cost_saved_usd },
                      ]}
                      index="scenario"
                      category="usd"
                      color="graph"
                      height={220}
                      valueFormatter={(v) => `$${v.toFixed(2)}`}
                    />
                  </div>
                )}
                <Receipt
                  className="mt-auto"
                  origin={`summary.baseline_cost_usd · every call repriced at ${config.baseline_model}`}
                />
              </CardBody>
            </Card>

            <Card className="flex min-w-0 flex-col">
              <CardHeader as="h3" eyebrow="summary.by_role" title="Prompt against completion" />
              <CardBody className="flex min-h-0 flex-1 flex-col gap-4">
                {!metered || promptTokens + completionTokens === 0 ? (
                  <NotMetered figure="Token split" />
                ) : (
                  <div className="min-w-0">
                    <DonutChart
                      data={tokenSplit}
                      height={200}
                      centerLabel={(promptTokens + completionTokens).toLocaleString()}
                      centerSub="tokens"
                      valueFormatter={(v) => v.toLocaleString()}
                    />
                  </div>
                )}
                <Receipt
                  className="mt-auto"
                  origin="summary.by_role[].prompt_tokens + completion_tokens"
                />
              </CardBody>
            </Card>

            <Card className="flex min-w-0 flex-col">
              <CardHeader as="h3" eyebrow="summary.by_role" title="Cost by role" />
              <CardBody className="flex min-h-0 flex-1 flex-col gap-4">
                {!metered ? (
                  <NotMetered figure="Cost by role" />
                ) : (
                  <RankedBars
                    label="Metered cost by role"
                    data={roleRows.map(([role, r]) => ({ name: role, value: r.cost_usd }))}
                    valueFormatter={(v) => `$${v.toFixed(2)}`}
                    color="graph"
                    maxRows={6}
                  />
                )}
                <Receipt className="mt-auto" origin="summary.by_role[].cost_usd" />
              </CardBody>
            </Card>

            <Card className="flex min-w-0 flex-col">
              <CardHeader as="h3" eyebrow="summary.by_role" title="Calls by role" />
              <CardBody className="flex min-h-0 flex-1 flex-col gap-4">
                {!metered ? (
                  <NotMetered figure="Calls by role" />
                ) : (
                  <RankedBars
                    label="Metered calls by role"
                    data={roleRows.map(([role, r]) => ({ name: role, value: r.calls }))}
                    valueFormatter={(v) => v.toLocaleString()}
                    color="ml"
                    maxRows={6}
                  />
                )}
                <Receipt className="mt-auto" origin="summary.by_role[].calls" />
              </CardBody>
            </Card>
          </div>

          {/* ── Per-role breakdown ────────────────────────────────────────────── */}
          <DataPanel
            eyebrow="aegis.gateway"
            title="Per-role usage"
            collapsible
            maxHeight={420}
            actions={
              <Badge tone="neutral" className="font-mono">
                {roleRows.length} roles
              </Badge>
            }
          >
            {roleRows.length === 0 ? (
              /* "Spend being routed and held down" — there is nothing to hold down
                 yet, and the words below say so. */
              <SceneState name="cost" size="md">
                <Absence
                  className="text-left"
                  figure="Per-role usage"
                  why="No gateway call has been metered yet."
                />
              </SceneState>
            ) : (
              <Table className="min-w-[640px]">
                <THead>
                  <TH>role</TH>
                  <TH className="text-right">calls</TH>
                  <TH className="text-right">tokens</TH>
                  <TH className="text-right">cost</TH>
                  <TH className="text-right">model tier</TH>
                </THead>
                <TBody>
                  {roleRows.map(([role, r]) => (
                    <TR key={role}>
                      <TD>
                        <Figure className="break-words text-foreground">{role}</Figure>
                      </TD>
                      <TD className="text-right">
                        <Figure className="text-foreground">{r.calls.toLocaleString()}</Figure>
                      </TD>
                      <TD className="text-right">
                        <Figure className="text-muted-foreground">
                          {(r.prompt_tokens + r.completion_tokens).toLocaleString()}
                        </Figure>
                      </TD>
                      <TD className="text-right">
                        <Figure className="text-foreground">{usd(r.cost_usd)}</Figure>
                      </TD>
                      <TD className="text-right">
                        <Badge tone={r.small_model ? 'ok' : 'neutral'}>
                          {r.small_model ? 'small' : 'frontier'}
                        </Badge>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </DataPanel>

          {/* ── Routing config ────────────────────────────────────────────────── */}
          <DataPanel
            eyebrow="aegis.gateway"
            title="Role → model"
            collapsible
            maxHeight={420}
            actions={
              <Badge tone="neutral" className="gap-1.5">
                <Route className="size-3" aria-hidden />
                read-only
              </Badge>
            }
          >
            <Table className="min-w-[720px]">
              <THead>
                <TH>role</TH>
                <TH>model</TH>
                <TH>unit cost</TH>
                <TH>fallback chain</TH>
              </THead>
              <TBody>
                {Object.entries(config.routing).map(([role, model]) => {
                  const chain = config.fallbacks[role] ?? []
                  return (
                    <TR key={role} className="align-top">
                      <TD>
                        <span className="inline-flex flex-wrap items-center gap-1.5">
                          <Figure className="break-words text-foreground">{role}</Figure>
                          {role === config.baseline_role ? (
                            <Badge tone="block">baseline</Badge>
                          ) : null}
                        </span>
                      </TD>
                      <TD>
                        <Figure className="break-words text-muted-foreground">{model}</Figure>
                      </TD>
                      <TD>
                        <Figure className="break-words text-muted-foreground">
                          {priceLabel(priced.get(role))}
                        </Figure>
                      </TD>
                      <TD>
                        {chain.length > 0 ? (
                          <span className="flex min-w-0 flex-wrap items-center gap-1">
                            {chain.map((step, i) => (
                              <span key={step} className="inline-flex items-center gap-1">
                                {i > 0 ? (
                                  <ArrowRight
                                    className="size-3 text-muted-foreground/60"
                                    aria-hidden
                                  />
                                ) : null}
                                <span
                                  translate="no"
                                  className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[0.72rem] break-words text-foreground"
                                >
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
                      </TD>
                    </TR>
                  )
                })}
              </TBody>
            </Table>
          </DataPanel>

          {/* ── Gateway limits ────────────────────────────────────────────────── */}
          <Card className="min-w-0">
            <CardHeader as="h3" eyebrow="aegis.gateway" title="Gateway limits" />
            <CardBody className="@container">
              <div className="grid min-w-0 gap-3 @sm:grid-cols-2 @3xl:grid-cols-4">
                <div className="flex min-w-0 flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow inline-flex items-center gap-1.5">
                    <Timer className="size-3" aria-hidden /> timeout
                  </span>
                  <Figure className="break-words text-foreground">
                    {`${config.timeout_seconds}s`}
                  </Figure>
                </div>
                <div className="flex min-w-0 flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow">max output tokens</span>
                  <Figure className="break-words text-foreground">
                    {config.max_output_tokens.toLocaleString()}
                  </Figure>
                </div>
                <div className="flex min-w-0 flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow">baseline role</span>
                  <Figure className="break-words text-foreground">{config.baseline_role}</Figure>
                </div>
                <div className="flex min-w-0 flex-col gap-1 rounded-lg border border-border bg-surface-2/40 p-3.5">
                  <span className="eyebrow">baseline model</span>
                  <Figure className="break-words text-foreground">{config.baseline_model}</Figure>
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
