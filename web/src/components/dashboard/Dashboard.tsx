'use client'

import { ChevronDown, DatabaseZap, ListChecks, Route, Target, Timer, Zap } from 'lucide-react'
import { type ReactElement, type ReactNode } from 'react'

import { StatCard } from '@/components/metrics/StatCard'
import { BentoGrid, BentoTile } from '@/components/shared/BentoGrid'
import { KpiHero } from '@/components/shared/KpiHero'
import { Card } from '@/components/primitives/card'
import { Gauge } from '@/components/primitives/Gauge'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { useAuth } from '@/lib/auth/AuthContext'
import { useMetricsSeries } from '@/state/useMetrics'
import type { Role } from '@/lib/stream'

import DashboardCharts from './DashboardCharts'
import { MyBudgetTile } from './MyBudgetTile'
import { costSavedTrend, reductionPct, sessionSavedDelta } from './overview'
import { formatUsd } from './roi'
import { RoiPanel } from './RoiPanel'
import { RoutingTable } from './RoutingTable'
import { ValueSpine } from './ValueSpine'

/** A small section kicker (eyebrow + optional detail tooltip). */
function SectionHead({ label, info }: { label: string; info?: ReactNode }): ReactElement {
  return (
    <div className="flex items-center gap-2">
      <span className="eyebrow text-foreground">{label}</span>
      {info != null && <InfoTip label={label}>{info}</InfoTip>}
    </div>
  )
}

/** A collapsible "one layer down" disclosure, styled as a calm card. */
function Expander({ summary, children }: { summary: string; children: ReactNode }): ReactElement {
  return (
    <Card className="overflow-hidden">
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3.5 select-none">
          <span className="t-title text-foreground">{summary}</span>
          <ChevronDown className="ml-auto size-4 text-muted-foreground transition-transform duration-200 group-open:rotate-180" />
        </summary>
        <div className="border-t border-border/70 px-5 py-4">{children}</div>
      </details>
    </Card>
  )
}

/** The Quality gauge tile — a live grounding-proxy read-out. */
function QualityTile({ quality }: { quality: number | null }): ReactElement {
  return (
    <BentoTile span={4} reveal index={2}>
      <div className="flex h-full flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="grid size-6 shrink-0 place-items-center rounded-md bg-ml/12">
            <Target className="size-3.5 text-ml-ink" />
          </span>
          <span className="eyebrow">Quality</span>
          <InfoTip label="About Quality">
            Share of completed runs that retrieved backing context before
            answering — a measured grounding proxy from GET /metrics, not an
            LLM-judge score.
          </InfoTip>
          {quality != null && (
            <span
              className="animate-pip ml-auto size-1.5 shrink-0 rounded-full bg-ok"
              style={{ ['--pip-color' as string]: 'var(--ok)' }}
              title="live from /metrics"
            />
          )}
        </div>
        <div className="grid flex-1 place-items-center">
          {quality == null ? (
            <span className="t-metric text-muted-foreground">—</span>
          ) : (
            <Gauge value={quality} color="ml" size={116} />
          )}
        </div>
      </div>
    </BentoTile>
  )
}

/**
 * The Overview surface — the surface leadership sees, built as a non-linear bento
 * with the **money as the hero**: a giant live "Cost saved" KpiHero, a band of
 * operational stats (queries · actions · quality · latency · cache), a row of
 * real recharts (cost trend · model mix · query volume), and a four-tile value
 * spine (Savings · Security · Performance · Audit). Every live figure carries a
 * green dot and every illustrative one keeps its honest "sample" badge.
 */
export function Dashboard({ role, token }: { role: Role; token: string | null }): ReactElement {
  const series = useMetricsSeries(token)
  const metrics = series.latest
  const isAdmin = role === 'admin'

  const saved = metrics?.cost_saved_usd ?? null
  const baseline = metrics?.baseline_cost_usd ?? null
  const costPer1k = metrics?.cost_per_1k_queries_usd ?? null
  const cacheHit = metrics?.cache_hit_rate ?? null
  const quality = metrics?.quality_score ?? null
  const llmCalls = metrics?.total_calls ?? null
  const actionsApproved = metrics?.actions_approved ?? null
  const p95Ms = metrics?.p95_latency_ms ?? null
  const reduction = reductionPct(baseline, costPer1k)
  const savedTrend = costSavedTrend(series.history)
  const savedDelta = sessionSavedDelta(series.history)

  // The "Value" spine — the four outcomes leadership signs off on. Admin (the
  // oversight role) leads with it at the very top; the other roles see it after
  // the hero + charts.
  const valueSection = (
    <section className="flex flex-col gap-3">
      <SectionHead
        label="Value"
        info="The four things leadership signs off on — savings, security, performance at least cost, and auditability. Live figures carry a green dot; illustrative ones are badged sample."
      />
      <BentoGrid>
        <ValueSpine metrics={metrics} />
      </BentoGrid>
    </section>
  )

  return (
    <div className="flex flex-col gap-8">
      {isAdmin && valueSection}
      {/* ── Hero band — money leads, operational stats fill beside it. ── */}
      <BentoGrid>
        {saved != null ? (
          <KpiHero
            className="col-span-12 lg:col-span-8 lg:row-span-2"
            label="Cost saved"
            value={saved}
            format={(n) => formatUsd(n, 0)}
            signal="ok"
            trend={savedTrend}
            delta={
              reduction != null
                ? { value: reduction, direction: 'up', tone: 'good', suffix: '% vs frontier' }
                : savedDelta != null
                  ? { value: Math.round(savedDelta), direction: 'up', tone: 'good', suffix: 'this session' }
                  : undefined
            }
            info="Cumulative USD saved versus running every query on the frontier model — measured live from GET /metrics. The tally comes from small-model routing; cache hits bypass it, so this is the conservative figure."
          />
        ) : (
          <Card className="col-span-12 flex min-h-[200px] items-center justify-center p-6 shadow-pop lg:col-span-8 lg:row-span-2">
            <span className="text-sm text-muted-foreground">Awaiting live metrics…</span>
          </Card>
        )}

        <BentoTile span={4} reveal index={0}>
          <StatCard
            label="LLM calls"
            value={llmCalls}
            icon={Zap}
            signal="graph"
            live={llmCalls != null}
            info="Chat completions served since the backend started — measured from the gateway usage tally on GET /metrics. Process-wide (not per-day); resets on restart."
          />
        </BentoTile>

        <BentoTile span={4} reveal index={1}>
          <StatCard
            label="Actions approved"
            value={actionsApproved}
            icon={ListChecks}
            signal="ok"
            live={actionsApproved != null}
            info="Human-gated actions cleared to the terminal approved state — counted from the durable approvals store on GET /metrics."
          />
        </BentoTile>

        <QualityTile quality={quality} />

        <BentoTile span={4} reveal index={3}>
          <StatCard
            label="p95 latency"
            value={p95Ms}
            format={(n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${Math.round(n)} ms`)}
            icon={Timer}
            signal="graph"
            live={p95Ms != null}
            info="95th-percentile whole-run duration from the backend's per-process latency window on GET /metrics. Dash until a run has been recorded — an honest empty state, not a fabricated figure."
          />
        </BentoTile>

        <BentoTile span={4} reveal index={4}>
          <MyBudgetTile token={token} />
        </BentoTile>

        <BentoTile span={4} reveal index={5}>
          <StatCard
            label="Cache hit"
            value={cacheHit != null ? cacheHit * 100 : null}
            format={(n) => `${Math.round(n)}%`}
            icon={DatabaseZap}
            signal="ok"
            live={cacheHit != null}
            info="Share of requests served from the fast cache path — measured live from GET /metrics."
          />
        </BentoTile>
      </BentoGrid>

      {/* ── Charts row — real recharts, drawing in on scroll. ── */}
      <BentoGrid>
        <DashboardCharts metrics={metrics} history={series.history} />
      </BentoGrid>

      {/* ── Value spine — the four outcomes a buyer signs off on (admin sees it
          at the very top instead). ── */}
      {!isAdmin && valueSection}

      {/* ── Detail, one layer down. ── */}
      <div className="flex flex-col gap-4">
        <Expander summary="Cost breakdown">
          <RoiPanel metrics={metrics} />
        </Expander>
        {isAdmin && metrics && (
          <Expander summary="Model routing">
            <div className="flex items-center gap-2 pb-3">
              <Route className="size-3.5 text-agent-ink" />
              <span className="eyebrow">Role → deployment</span>
              <InfoTip label="About model routing">
                How heterogeneous routing sends cheap work to small models — the
                mechanism behind the small-model share and the savings.
              </InfoTip>
            </div>
            <RoutingTable routing={metrics.routing} />
          </Expander>
        )}
      </div>
    </div>
  )
}

/** Client entry for the Overview section — gated on a reachable backend. */
export function DashboardMount({ role }: { role: Role }): ReactElement {
  // `/metrics` is RBAC-scoped: hand the dashboard the real session bearer, and
  // hold it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
      <TooltipProvider>
        <Dashboard role={role} token={session?.token ?? null} />
      </TooltipProvider>
    </BackendGate>
  )
}
