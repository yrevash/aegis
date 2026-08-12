'use client'

import { Gauge, Loader2, Ruler, ShieldAlert, SlidersHorizontal } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { RISK_TONE } from './opsShared'
import type { OpsParamsResponse } from '@/lib/api/platform'

/** A labelled chip list of terms, or an em-dash when empty. */
function TermChips({
  terms,
  tone,
}: {
  terms: string[]
  tone: 'block' | 'risk'
}): ReactElement {
  if (terms.length === 0) return <span className="text-sm text-muted-foreground">—</span>
  return (
    <div className="flex flex-wrap gap-1.5">
      {terms.map((t) => (
        <Badge key={t} tone={tone} className="font-mono text-[0.62rem]">
          {t}
        </Badge>
      ))}
    </div>
  )
}

interface Props {
  params: OpsParamsResponse | null
  loading: boolean
  error: string | null
}

/**
 * The **Loop parameters** panel (`GET /ops/params`) — the tunable knobs the
 * tiered release gate runs on, shown read-only for the operator: the eval margin
 * a draft must clear, the diff-size fractions that bucket a change low/high, the
 * safety + critical-config term lists that force a human gate, the config keys
 * the loop may auto-tune (with their max per-release delta), and the autonomy
 * ceiling above which nothing auto-promotes.
 */
export function LoopParams({ params, loading, error }: Props): ReactElement {
  const tunable = params
    ? params.tunable_config_keys.map((k) => ({ key: k, max: params.tunable_max_delta[k] }))
    : []

  return (
    <Card>
      <CardHeader
        eyebrow="GET /ops/params"
        title="Loop parameters"
        description="The knobs the tiered release gate runs on — read-only config for the operator."
        actions={
          !loading && !error && params ? (
            <Badge tone={RISK_TONE[params.auto_promote_ceiling] ?? 'neutral'} className="gap-1.5">
              <Gauge className="size-3" />
              auto-promote ≤ {params.auto_promote_ceiling}
            </Badge>
          ) : null
        }
      />
      <CardBody className="space-y-5">
        {error ? (
          <p className="py-8 text-center text-sm text-danger">{error}</p>
        ) : loading || params == null ? (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading loop parameters…
          </div>
        ) : (
          <>
            {/* Headline numeric knobs */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard
                label="Eval margin"
                value={params.eval_margin.toFixed(3)}
                icon={Ruler}
                tone="agent"
              />
              <StatCard
                label="High-diff fraction"
                value={`${Math.round(params.high_diff_fraction * 100)}%`}
                icon={SlidersHorizontal}
                tone="block"
              />
              <StatCard
                label="Low-diff fraction"
                value={`${Math.round(params.low_diff_fraction * 100)}%`}
                icon={SlidersHorizontal}
                tone="ok"
              />
              <StatCard
                label="Auto-promote ceiling"
                value={params.auto_promote_ceiling}
                icon={Gauge}
                tone="ml"
              />
            </div>

            {/* Term lists that force a human gate */}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div className="rounded-xl border border-border bg-surface-2/40 p-4">
                <div className="mb-2 flex items-center gap-1.5">
                  <ShieldAlert className="size-3.5 text-block-ink" />
                  <span className="eyebrow">safety terms</span>
                  <span className="ml-auto font-mono text-[0.62rem] text-muted-foreground">
                    {params.safety_terms.length}
                  </span>
                </div>
                <p className="mb-2.5 text-[0.72rem] leading-snug text-muted-foreground">
                  A draft touching any of these is treated as guardrail-adjacent and cannot auto-ship.
                </p>
                <TermChips terms={params.safety_terms} tone="block" />
              </div>
              <div className="rounded-xl border border-border bg-surface-2/40 p-4">
                <div className="mb-2 flex items-center gap-1.5">
                  <ShieldAlert className="size-3.5 text-risk-ink" />
                  <span className="eyebrow">critical config markers</span>
                  <span className="ml-auto font-mono text-[0.62rem] text-muted-foreground">
                    {params.critical_config_markers.length}
                  </span>
                </div>
                <p className="mb-2.5 text-[0.72rem] leading-snug text-muted-foreground">
                  Config keys matching these (model, tools, permissions…) escalate the risk tier.
                </p>
                <TermChips terms={params.critical_config_markers} tone="risk" />
              </div>
            </div>

            {/* Auto-tunable config keys + their per-release cap */}
            <div>
              <p className="eyebrow mb-2">auto-tunable config keys · max Δ per release</p>
              <div className="overflow-hidden rounded-xl border border-border">
                <Table>
                  <THead>
                    <TH>config key</TH>
                    <TH className="text-right">max delta</TH>
                  </THead>
                  <TBody>
                    {tunable.length === 0 ? (
                      <TR>
                        <TD className="text-muted-foreground">— none tunable</TD>
                        <TD className="text-right text-muted-foreground">—</TD>
                      </TR>
                    ) : (
                      tunable.map((t) => (
                        <TR key={t.key}>
                          <TD className="font-mono">{t.key}</TD>
                          <TD className="tabular text-right font-mono">
                            {t.max != null ? `±${t.max}` : '—'}
                          </TD>
                        </TR>
                      ))
                    )}
                  </TBody>
                </Table>
              </div>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  )
}
