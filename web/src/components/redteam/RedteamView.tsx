'use client'

import {
  AlertTriangle,
  Loader2,
  Play,
  ShieldCheck,
  Swords,
  Target,
} from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { runRedteam } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type { RedteamCategoryReport, RedteamReportResponse } from '@/lib/api/platform'

/** Attack categories rendered as bars, in a stable order (benign_control is the FP measure — excluded). */
const CATEGORY_ORDER = [
  'prompt_injection',
  'jailbreak',
  'system_prompt_leak',
  'pii_extraction',
  'content_safety',
] as const

/** Round a 0–1 rate to a whole-percent string. */
function pct(rate: number): string {
  return `${Math.round(rate * 100)}%`
}

/** Human label for a snake_case category id. */
function categoryLabel(id: string): string {
  return id.replace(/_/g, ' ')
}

/**
 * Per-category block-rate bars — one row per attack category with a block-rate
 * bar (green at/above the gate threshold, block-red below), the N blocked/total
 * count, and the leaked probe ids (attacks that got through) called out inline.
 */
function CategoryBars({
  categories,
  minBlockRate,
}: {
  categories: RedteamCategoryReport[]
  minBlockRate: number
}): ReactElement {
  // Attack categories only, in the canonical order; drop the benign-control row.
  const byId = new Map(categories.map((c) => [c.category, c]))
  const rows = CATEGORY_ORDER.map((id) => byId.get(id)).filter(
    (c): c is RedteamCategoryReport => c != null,
  )

  return (
    <div className="flex flex-col gap-4">
      {rows.map((c) => {
        const good = c.blockRate >= minBlockRate
        const hex = good ? 'var(--ok)' : 'var(--block)'
        return (
          <div key={c.category} className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-medium capitalize text-foreground">
                {categoryLabel(c.category)}
              </span>
              <span className="flex items-center gap-2">
                <span className="tabular font-mono text-[0.72rem] text-muted-foreground">
                  {c.blocked}/{c.total} blocked
                </span>
                <span
                  className="tabular w-11 text-right font-mono text-[0.8rem] font-semibold"
                  style={{ color: hex }}
                >
                  {pct(c.blockRate)}
                </span>
              </span>
            </div>
            <div className="relative h-3 rounded-sm bg-muted/50">
              <div
                className="absolute inset-y-0 left-0 rounded-sm"
                style={{ width: `${Math.max(c.blockRate * 100, 1.5)}%`, background: hex }}
              />
            </div>
            {c.leaked.length > 0 ? (
              <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[0.72rem] text-muted-foreground">
                <span className="inline-flex items-center gap-1 text-block-ink">
                  <AlertTriangle className="size-3" /> leaked
                </span>
                {c.leaked.map((id) => (
                  <Badge key={id} tone="block" className="font-mono text-[0.66rem]">
                    {id}
                  </Badge>
                ))}
              </p>
            ) : (
              <p className="mt-0.5 text-[0.72rem] text-[color:var(--success)]">
                all blocked · nothing leaked
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

/**
 * Red-team dashboard — the full offline attack-battery report from `POST
 * /redteam/run`. Headline block-rate KPIs (block-rate, gate pass/fail, attacks
 * run, false-positive rate) over honest measured numbers, per-category bars with
 * the leaked probe ids called out, and a Run-red-team action that re-runs the
 * battery — the endpoint runs it deterministically, with no LLM and no spend.
 */
function RedteamView(): ReactElement {
  // Live session token — see `AdminCommandCenter`: a constant `null` would fetch
  // with no bearer on a reload and, being constant in the dependency array below,
  // never retry once `AuthProvider` restored the persisted session.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  const [report, setReport] = useState<RedteamReportResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  function load(): void {
    setRunning(true)
    setError(null)
    runRedteam(token)
      .then((r) => setReport(r))
      .catch(() => setError('Could not run the red-team battery. Is the backend running?'))
      .finally(() => setRunning(false))
  }

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    setRunning(true)
    runRedteam(token)
      .then((r) => {
        if (alive) setReport(r)
      })
      .catch(() => {
        if (alive) setError('Could not run the red-team battery. Is the backend running?')
      })
      .finally(() => {
        if (alive) setRunning(false)
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  const overall = report?.overall

  return (
    <div className="space-y-6">
      {/* Section header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="eyebrow mb-1">attacks · block-rate</p>
          <h1 className="t-hero text-foreground">Red-team</h1>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={running}
          className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-block px-4 py-2.5 text-sm font-medium text-white shadow-card transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {running ? (
            <>
              <Loader2 className="size-4 animate-spin" /> Running…
            </>
          ) : (
            <>
              <Play className="size-4" /> Run red-team
            </>
          )}
        </button>
      </div>

      {error ? (
        <Card>
          <CardBody>
            <p className="py-8 text-center text-sm text-danger">{error}</p>
          </CardBody>
        </Card>
      ) : report == null || overall == null ? (
        <Card>
          <CardBody>
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Running the offline battery…
            </div>
          </CardBody>
        </Card>
      ) : (
        <>
          {/* ── Headline KPIs ─────────────────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Overall block rate"
              value={pct(overall.blockRate)}
              icon={Swords}
              tone={report.passed ? 'ok' : 'block'}
            />
            <StatCard
              label="Gate"
              value={report.passed ? 'PASS' : 'FAIL'}
              icon={ShieldCheck}
              tone={report.passed ? 'ok' : 'block'}
            />
            <StatCard
              label="Attacks run"
              value={`${overall.attacksBlocked}/${overall.attacksTotal}`}
              icon={Target}
              tone="neutral"
            />
            <StatCard
              label="False-positive rate"
              value={pct(overall.falsePositiveRate)}
              icon={AlertTriangle}
              tone={overall.falsePositiveRate <= report.thresholds.maxFalsePositiveRate ? 'ok' : 'block'}
            />
          </div>

          {/* ── Per-category block-rate bars ──────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis · /redteam/run"
              title="Block rate by attack category"
              description="One row per attack category — block-rate bar, N blocked/total, and the leaked probe ids that got through."
              actions={
                <Badge tone={report.passed ? 'ok' : 'block'} className="uppercase">
                  {report.passed ? 'gate passed' : 'gate failed'}
                </Badge>
              }
            />
            <CardBody className="pt-0">
              <CategoryBars
                categories={report.categories}
                minBlockRate={report.thresholds.minBlockRate}
              />
            </CardBody>
          </Card>

          {/* ── Honest provenance / caveat ────────────────────────────────────── */}
          <p className="text-[0.72rem] leading-snug text-muted-foreground">
            Deterministic offline battery — gate is ≥ {pct(report.thresholds.minBlockRate)} block
            rate and ≤ {pct(report.thresholds.maxFalsePositiveRate)} false-positive across{' '}
            {overall.controlsTotal} benign controls. The leaked probes are the{' '}
            <span className="font-mono text-foreground">needs_llm</span> semantic attacks the
            deterministic backstop can&apos;t catch on its own — the live model-layer classifier
            catches those; offline they are reported as leaks rather than hidden.
          </p>
        </>
      )}
    </div>
  )
}

/** Client entry for the Red-team section — gated on a reachable backend. */
export function RedteamMount(): ReactElement {
  return (
    <BackendGate>
      <RedteamView />
    </BackendGate>
  )
}
