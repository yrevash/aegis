'use client'

import { Gauge, Ruler, ShieldAlert, SlidersHorizontal } from 'lucide-react'
import type { ReactElement } from 'react'

import { InfoTip } from '@/components/primitives/InfoTip'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import type { OpsParamsResponse } from '@/lib/api/platform'

/** A labelled chip list of terms, or an em-dash when empty. */
function TermChips({
  terms,
  tone,
}: {
  terms: string[]
  tone: 'block' | 'risk'
}): ReactElement {
  if (terms.length === 0)
    return <span className="text-sm text-muted-foreground italic">none configured</span>
  return (
    <div className="flex flex-wrap gap-1.5">
      {terms.map((t) => (
        <Badge key={t} tone={tone} className="font-mono text-[0.6875rem]">
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
        actions={
          <InfoTip label="About the loop parameters">
            Read-only here: these are the knobs the tiered release gate itself runs on.
          </InfoTip>
        }
      />
      <CardBody className="@container min-w-0 space-y-5">
        {error ? (
          <ErrorState error={error} />
        ) : loading || params == null ? (
          <LoadingState rows={4} label="Loading loop parameters…" />
        ) : (
          <>
            {/* Headline numeric knobs */}
            <div className="grid min-w-0 grid-cols-1 gap-4 @sm:grid-cols-2 @3xl:grid-cols-4">
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
            <div className="grid min-w-0 grid-cols-1 gap-4 @2xl:grid-cols-2">
              <div className="min-w-0 rounded-lg border border-border bg-surface-2/40 p-4">
                <div className="mb-2.5 flex items-center gap-1.5">
                  <ShieldAlert className="size-3.5 shrink-0 text-block-ink" aria-hidden />
                  <span className="eyebrow">safety terms</span>
                  <InfoTip label="About safety terms">
                    A draft touching any of these cannot auto-ship.
                  </InfoTip>
                  <span className="tabular ml-auto font-mono text-xs text-muted-foreground">
                    {params.safety_terms.length}
                  </span>
                </div>
                <TermChips terms={params.safety_terms} tone="block" />
              </div>
              <div className="min-w-0 rounded-lg border border-border bg-surface-2/40 p-4">
                <div className="mb-2.5 flex items-center gap-1.5">
                  <ShieldAlert className="size-3.5 shrink-0 text-risk-ink" aria-hidden />
                  <span className="eyebrow">critical config markers</span>
                  <InfoTip label="About critical config markers">
                    Config keys matching these escalate the risk tier.
                  </InfoTip>
                  <span className="tabular ml-auto font-mono text-xs text-muted-foreground">
                    {params.critical_config_markers.length}
                  </span>
                </div>
                <TermChips terms={params.critical_config_markers} tone="risk" />
              </div>
            </div>

            {/* Auto-tunable config keys + their per-release cap */}
            <div className="min-w-0">
              <p className="eyebrow mb-2">auto-tunable config keys · max Δ per release</p>
              <div className="min-w-0 overflow-hidden rounded-lg border border-border">
                <Table>
                  <THead>
                    <TH>config key</TH>
                    <TH className="text-right">max delta</TH>
                  </THead>
                  <TBody>
                    {tunable.length === 0 ? (
                      <TR>
                        <TD className="text-muted-foreground">— none tunable</TD>
                        <TD className="text-right text-xs text-muted-foreground italic">
                          not bounded
                        </TD>
                      </TR>
                    ) : (
                      tunable.map((t) => (
                        <TR key={t.key}>
                          <TD className="font-mono break-words">{t.key}</TD>
                          <TD className="tabular text-right font-mono">
                            {t.max != null ? `±${t.max}` : 'unbounded'}
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
