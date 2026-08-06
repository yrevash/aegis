import { ChevronDown, DatabaseZap, ListChecks, Route, Target, Timer, Zap } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { StatCard } from '@/components/metrics/StatCard'
import { BentoGrid, BentoTile } from '@/components/shared/BentoGrid'
import { KpiHero } from '@/components/shared/KpiHero'
import { Card } from '@/components/ui/card'
import { Gauge } from '@/components/ui/Gauge'
import { InfoTip } from '@/components/ui/InfoTip'
import { useMetricsSeries } from '@/state/useMetrics'
import type { Role } from '@/types/stream'

// Charts pull in `recharts`; the whole Overview surface is already route-lazy in
// `Portal`, so a plain import keeps the engine scoped to this chunk (it loads on
// navigation to Overview, not in the initial app bundle).
import DashboardCharts from './DashboardCharts'
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

/** The Quality gauge tile — a live grounding-proxy read-out (§4.2 band). */
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
 * The Overview surface (§4.2) — the surface leadership sees, rebuilt from a
 * linear stack into a non-linear bento with the **money as the hero**. The value
 * spine reads in one glance: a giant live "Cost saved" KpiHero, a band of
 * operational stats (queries · actions · quality · latency · cache), a row of
 * real recharts (cost trend · model mix · query volume), and a four-tile value
 * spine (Savings · Security · Performance · Audit). Every explanatory sentence
 * has moved to an ⓘ tooltip; every live figure carries a green dot and every
 * illustrative one keeps its honest "sample" badge. Cost detail and the admin
 * routing map live one layer down in expanders.
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
  const reduction = reductionPct(baseline, costPer1k)
  const savedTrend = costSavedTrend(series.history)
  const savedDelta = sessionSavedDelta(series.history)

  return (
    <div className="flex flex-col gap-8">
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
            label="Queries today"
            value={2870}
            icon={Zap}
            signal="graph"
            delta={{ value: 8, direction: 'up', tone: 'good', suffix: '%' }}
            sample
            info="Illustrative daily throughput — no backend counter is wired yet."
          />
        </BentoTile>

        <BentoTile span={4} reveal index={1}>
          <StatCard
            label="Actions approved"
            value={41}
            icon={ListChecks}
            signal="ok"
            sample
            info="Illustrative count of human-gated actions cleared — no backend counter is wired yet."
          />
        </BentoTile>

        <QualityTile quality={quality} />

        <BentoTile span={4} reveal index={3}>
          <StatCard
            label="p95 latency"
            value={1.9}
            format={(n) => `${n.toFixed(1)}s`}
            icon={Timer}
            signal="graph"
            sample
            info="Illustrative 95th-percentile response time — no backend latency histogram is wired yet."
          />
        </BentoTile>

        <BentoTile span={4} reveal index={4}>
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
        <DashboardCharts metrics={metrics} />
      </BentoGrid>

      {/* ── Value spine — the four outcomes a buyer signs off on. ── */}
      <section className="flex flex-col gap-3">
        <SectionHead
          label="Value"
          info="The four things leadership signs off on — savings, security, performance at least cost, and auditability. Live figures carry a green dot; illustrative ones are badged sample."
        />
        <BentoGrid>
          <ValueSpine metrics={metrics} />
        </BentoGrid>
      </section>

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
