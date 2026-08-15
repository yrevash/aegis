'use client'

import { ShieldCheck } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { SIGNALS } from '@/config/signals'

import {
  RESIDUAL_META,
  residualSignal,
  type RankedRisk,
  type Residual,
} from './riskRanking'

/**
 * The Risk Map ladder: every risk on one rung, worst residual first.
 *
 * This replaced a 5×5 likelihood × impact heat-map. With six risks that grid
 * was 20 empty cells, ~950px tall, and it forced the reader to match a tiny
 * `AA-03` pill against a card further down the page — the eye could not rank
 * anything. A ranked list does the ranking for the reader.
 *
 * Two channels, no more:
 *  - **Order + bar length** = inherent exposure (likelihood × impact) — before.
 *  - **Colour** = residual band after the mitigating control — after.
 *
 * So a long green bar reads exactly as intended: "a serious inherent risk that
 * the guardrail brought down". Colour has one meaning on this page and nowhere
 * competes with a second scale.
 */

/** Residual band → Badge variant (green / amber / red) — the single colour scale. */
const RESIDUAL_BADGE: Record<Residual, 'ok' | 'risk' | 'block'> = {
  low: 'ok',
  medium: 'risk',
  high: 'block',
}

interface RiskLadderProps {
  /** Risks already ranked worst-residual-first, with bar lengths derived. */
  ranked: RankedRisk[]
  /** Worst exposure the published scale allows — the bar denominator. */
  ceiling: number
}

export function RiskLadder({ ranked, ceiling }: RiskLadderProps): ReactElement {
  return (
    <ol className="divide-y divide-border">
      {ranked.map((entry) => (
        <Rung key={entry.risk.id} entry={entry} ceiling={ceiling} />
      ))}
    </ol>
  )
}

/** One risk: identity, its before/after measure, and the control that did it. */
function Rung({ entry, ceiling }: { entry: RankedRisk; ceiling: number }): ReactElement {
  const { risk, exposure, pct } = entry
  const meta = RESIDUAL_META[risk.residual]
  const token = SIGNALS[residualSignal(risk.residual)]

  return (
    <li className="grid grid-cols-1 gap-x-6 gap-y-2 px-5 py-3.5 transition-colors duration-150 hover:bg-surface-2/50 md:grid-cols-[minmax(0,1fr)_auto]">
      {/* Identity — id, name, OWASP-Agentic category. */}
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="font-mono text-[0.68rem] tabular text-muted-foreground">{risk.id}</span>
        <span className="t-title text-foreground">{risk.title}</span>
        <span className="eyebrow">{risk.category}</span>
      </div>

      {/* Measure — likelihood × impact, the exposure bar, the residual left over. */}
      <div className="flex items-center gap-3 md:w-[25rem]">
        <span
          className="w-[4.25rem] shrink-0 font-mono text-[0.68rem] tabular text-muted-foreground"
          title={`Likelihood ${risk.likelihood} × impact ${risk.impact}`}
        >
          L{risk.likelihood} × I{risk.impact}
        </span>
        <div
          className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-2 md:w-32 md:flex-none"
          role="img"
          aria-label={`Inherent exposure ${exposure} of ${ceiling} — likelihood ${risk.likelihood} times impact ${risk.impact}`}
        >
          <div className="h-full rounded-full" style={{ width: `${pct}%`, background: token.hex }} />
        </div>
        <span className="tabular w-6 shrink-0 text-right font-display text-sm leading-none font-semibold text-foreground">
          {exposure}
        </span>
        <Badge variant={RESIDUAL_BADGE[risk.residual]} className="shrink-0">
          {meta.label} residual
        </Badge>
      </div>

      {/* What the control does — one reading order, no second card to hunt for. */}
      <div className="flex min-w-0 flex-col gap-1 md:col-span-2">
        <p className="t-body max-w-[80ch] text-foreground/90">{risk.mitigation}</p>
        <p className="flex items-center gap-1.5 text-[0.78rem] text-agent-ink">
          <ShieldCheck className="size-3.5 shrink-0" aria-hidden />
          {risk.control_ref}
        </p>
      </div>
    </li>
  )
}
