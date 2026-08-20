'use client'

import { CheckCircle2, Gauge, HelpCircle, ShieldCheck, XCircle } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { PageHeader } from '@/components/primitives/PageHeader'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { getEvalsReport } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type { EvalMetricConfig, EvalsReportResponse } from '@/lib/api/platform'
import { cn } from '@/lib/utils'

/** A minimal async-load result, mirroring the LLMOps view's fetch pattern. */
interface Loaded<T> {
  data: T | null
  loading: boolean
  error: string | null
}

/**
 * Load with an async call, re-running whenever `key` changes.
 *
 * `key` carries the bearer token, so the fetch re-fires once `AuthProvider` has
 * restored the persisted session (its effect runs *after* this one on a reload).
 * `enabled` holds the call back until then, so it never fires without a bearer
 * and 401s into a permanently-stuck state.
 */
function useLoad<T>(fn: () => Promise<T>, key: string | null, enabled: boolean): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, loading: true, error: null })
  useEffect(() => {
    if (!enabled) return
    let alive = true
    setState((prev) => ({ ...prev, loading: true }))
    fn()
      .then((data) => {
        if (alive) setState({ data, loading: false, error: null })
      })
      .catch(() => {
        // Always resolve `loading` — a swallowed failure would spin forever.
        if (alive)
          setState({ data: null, loading: false, error: 'Could not load. Is the backend running?' })
      })
    return () => {
      alive = false
    }
    // `fn` is recreated per render; `key` + `enabled` are the real inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled])
  return state
}

/** Percent string for a [0,1] score (null → em dash). */
function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

/** Turn a raw metric id (context_precision@1) into a readable label. */
function metricLabel(name: string): string {
  const base = name
    .replace(/_/g, ' ')
    .replace(/@(\d+)/, ' @$1')
    .replace(/\baccuracy\b/, 'accuracy')
  return base.charAt(0).toUpperCase() + base.slice(1)
}

/** A short human gloss per known metric family (honest, no fabrication). */
function metricGloss(name: string): string {
  if (name.startsWith('context_precision')) return 'Is the right passage ranked at the top?'
  if (name.startsWith('context_recall')) return 'Did retrieval surface the gold passage at all?'
  if (name.startsWith('groundedness')) return 'Is the answer supported by the retrieved context?'
  if (name.startsWith('tool_selection')) return 'Did the agent pick the correct tool?'
  return 'Deterministic overlap metric — no LLM.'
}

/** One metric card: value, threshold, direction, pass/fail, case count. */
function MetricCard({ m }: { m: EvalMetricConfig }): ReactElement {
  const pass = m.passed
  const cmp = m.higherIsBetter ? '≥' : '≤'
  return (
    <div
      className={cn(
        // `transition-shadow` was transitioning a shadow this card does not have:
        // DESIGN.md §4 keeps one shadow token for floating layers and a border
        // everywhere else.
        'rounded-lg border bg-card p-5 md:p-6',
        pass ? 'border-ok/40' : 'border-block/50',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">{metricLabel(m.name)}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{metricGloss(m.name)}</p>
        </div>
        <Badge tone={pass ? 'ok' : 'block'} className="shrink-0">
          {pass ? <CheckCircle2 className="size-3.5" aria-hidden /> : <XCircle className="size-3.5" aria-hidden />}
          {pass ? 'Pass' : 'Fail'}
        </Badge>
      </div>

      <div className="mt-4 flex items-end justify-between">
        <div>
          {/*
            The passing figure used to be painted green. The Pass/Fail badge sits
            two lines above it carrying the same verdict with an icon and a word,
            so the colour was restating a decision the reader had already been
            given — and once every passing metric on the page is green, green
            stops being information. DESIGN.md §2 spends colour where it means
            something: the figure stays in foreground ink, and only a failure is
            coloured, because a failure is the thing worth finding by eye.
          */}
          <Figure size="stat" className={pass ? 'text-foreground' : 'text-danger'}>
            {pct(m.value)}
          </Figure>
          <p className="mt-1 text-xs text-muted-foreground">
            threshold {cmp} <Figure className="text-xs leading-4">{pct(m.threshold)}</Figure>
          </p>
        </div>
        <span className="text-xs text-muted-foreground">
          <Figure className="text-xs leading-4">{m.cases}</Figure>{' '}
          {m.cases === 1 ? 'case' : 'cases'}
        </span>
      </div>
    </div>
  )
}

/**
 * Evals — the offline regression gate. Reads `getEvalsReport()` (→ `/evals/report`),
 * the deterministic RAGAS/DeepEval-pattern gate scored with **no LLM**. Leads with the
 * gate verdict (overall vs threshold + honest `source` badge), then a metric card per
 * `metric_configs` entry (value, threshold, direction, pass/fail, case count), an honest
 * "answer-relevancy not computed" panel (needs an LLM judge — never faked), and the
 * per-case breakdown table. Every figure comes straight from the accessor.
 */
function EvalsView(): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const report = useLoad<EvalsReportResponse>(() => getEvalsReport(token), token, hydrated)
  const data = report.data

  const metrics = data?.metrics ?? []
  const cases = data?.cases ?? []
  const overallPass = data?.passed ?? false
  // The gate passes iff every gated metric passes; its bar is each metric's own
  // threshold. We surface the tightest per-metric threshold as the headline bar.
  const gateThreshold = metrics.length
    ? Math.min(...metrics.map((m) => m.threshold))
    : null

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="retrieval quality · offline regression gate" title="Evals" />

      {report.error ? (
        <Card>
          <CardBody className="text-sm text-muted-foreground">{report.error}</CardBody>
        </Card>
      ) : null}

      {/* Row 1 — the gate verdict. */}
      <Card className={cn(overallPass ? 'ring-1 ring-ok/40' : 'ring-1 ring-block/50')}>
        <CardHeader
          eyebrow="release verdict"
          title="Regression gate"
          actions={
            <span
              className={cn(
                'grid size-8 place-items-center rounded-lg',
                overallPass ? 'bg-ok/15' : 'bg-block/20',
              )}
            >
              <ShieldCheck
                className={cn(
                  'size-4',
                  overallPass ? 'text-ok-ink' : 'text-block-ink',
                )}
              />
            </span>
          }
        />
        <CardBody>
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="flex items-center gap-4">
              <span
                className={cn(
                  'grid size-14 place-items-center rounded-lg',
                  overallPass ? 'bg-ok/15 text-ok-ink' : 'bg-block/20 text-block-ink',
                )}
              >
                {overallPass ? (
                  <CheckCircle2 className="size-7" aria-hidden />
                ) : (
                  <XCircle className="size-7" aria-hidden />
                )}
              </span>
              <div>
                <Figure size="display" className={overallPass ? 'text-foreground' : 'text-danger'}>
                  {pct(data?.overall)}
                </Figure>
                <p className="mt-1 text-sm text-muted-foreground">
                  mean score across {metrics.length} gated metric
                  {metrics.length === 1 ? '' : 's'}
                  {gateThreshold != null ? (
                    <>
                      {' '}· tightest bar ≥ <Figure>{pct(gateThreshold)}</Figure>
                    </>
                  ) : null}
                </p>
              </div>
            </div>
            <div className="flex flex-col items-start gap-2 sm:items-end">
              <Badge tone={overallPass ? 'ok' : 'block'}>
                {overallPass ? 'Gate passed' : 'Gate failed'}
              </Badge>
              {/* This was a badge that said `source:` — which is a receipt with a
                  border round it. It is now the one provenance treatment the
                  console shares (DESIGN.md §1). */}
              <Receipt
                origin={data?.source ?? 'not reported'}
                detail="deterministic · reproducible · no LLM judge in the loop"
                variant="inline"
                className="text-right"
              />
            </div>
          </div>
        </CardBody>
      </Card>

      {/* Row 2 — one card per computed metric. */}
      <div>
        <p className="eyebrow mb-3">per-metric readings</p>
        {report.loading ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-40 animate-pulse rounded-lg border border-border bg-surface-2/50"
              />
            ))}
          </div>
        ) : metrics.length ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {metrics.map((m) => (
              <MetricCard key={m.name} m={m} />
            ))}
          </div>
        ) : (
          <Card>
            <CardBody className="text-sm text-muted-foreground">No metrics reported.</CardBody>
          </Card>
        )}
      </div>

      {/* Row 3 — the honest answer-relevancy panel (never faked). */}
      <Card className="border-dashed">
        <CardHeader
          eyebrow="ragas · answer relevancy"
          title="Answer relevancy — not computed"
          actions={
            <span className="grid size-8 place-items-center rounded-lg bg-surface-2">
              <HelpCircle className="size-4 text-muted-foreground" aria-hidden />
            </span>
          }
        />
        <CardBody>
          <div className="flex flex-wrap items-center gap-3">
            <Badge tone="neutral">
              <Gauge className="size-3.5" aria-hidden />
              needs an LLM judge
            </Badge>
          </div>
        </CardBody>
      </Card>

      {/* Row 4 — the per-case breakdown. */}
      <Card>
        <CardHeader
          eyebrow="seed corpus"
          title="Per-case breakdown"
        />
        <CardBody className="px-0 py-0 md:px-0 md:py-0">
          {report.loading ? (
            <div role="status" className="px-6 py-8 text-sm text-muted-foreground">
              Loading cases…
            </div>
          ) : cases.length ? (
            <Table>
              <THead>
                <TH className="pl-6">Case</TH>
                <TH>Metric</TH>
                <TH className="text-right">Score</TH>
                <TH className="text-right">Threshold</TH>
                <TH className="pr-6 text-right">Verdict</TH>
              </THead>
              <TBody>
                {cases.flatMap((c) =>
                  c.metrics.map((m, i) => (
                    <TR key={`${c.name}-${m.name}`}>
                      <TD className="max-w-md pl-6">
                        {i === 0 ? (
                          <span className="line-clamp-2 text-sm text-foreground" title={c.name}>
                            {c.name}
                          </span>
                        ) : (
                          <span className="text-muted-foreground/40">↳</span>
                        )}
                      </TD>
                      <TD className="whitespace-nowrap font-mono text-xs text-muted-foreground">
                        {m.name}
                      </TD>
                      <TD className="text-right">
                        <Figure>{pct(m.value)}</Figure>
                      </TD>
                      <TD className="text-right text-muted-foreground">
                        <Figure>
                          {m.higherIsBetter ? '≥' : '≤'} {pct(m.threshold)}
                        </Figure>
                      </TD>
                      <TD className="pr-6 text-right">
                        <Badge tone={m.passed ? 'ok' : 'block'}>
                          {m.passed ? 'Pass' : 'Fail'}
                        </Badge>
                      </TD>
                    </TR>
                  )),
                )}
              </TBody>
            </Table>
          ) : (
            <div className="px-6 py-8 text-sm text-muted-foreground">
              The report carries no per-case rows.
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  )
}

/** Client entry for the Evals section — gated on a reachable backend. */
export function EvalsMount(): ReactElement {
  return (
    <BackendGate>
      <EvalsView />
    </BackendGate>
  )
}
