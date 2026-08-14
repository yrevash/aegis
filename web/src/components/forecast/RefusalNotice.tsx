'use client'

import { CalendarX2, PackageX, Ruler, TriangleAlert } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge, type BadgeTone } from '@/components/ui/Badge'
import type { ForecastRefusal } from '@/lib/api/types'

/** Headline, tone and icon for each refusal code. */
const COPY: Record<
  ForecastRefusal['code'],
  { title: string; tone: BadgeTone; label: string; icon: typeof Ruler }
> = {
  insufficient_history: {
    title: 'Not enough history to forecast',
    tone: 'risk',
    label: 'refused',
    icon: Ruler,
  },
  degenerate_series: {
    title: 'Nothing to forecast — the series is flat',
    tone: 'risk',
    label: 'refused',
    icon: CalendarX2,
  },
  fit_failed: {
    title: 'No model could be fitted to this series',
    tone: 'block',
    label: 'refused',
    icon: TriangleAlert,
  },
  extra_missing: {
    title: 'The forecasting extra is not installed',
    tone: 'neutral',
    label: 'unavailable',
    icon: PackageX,
  },
}

/**
 * A stated refusal, rendered as the result it is.
 *
 * This component exists so the console never draws a line it cannot justify. A
 * tenant with nine days of ledger sees "9 of 71 observations" and the reason the
 * shortfall matters — not an empty chart, not a spinner that never resolves, and
 * above all not a plausible-looking projection through six points.
 */
export function RefusalNotice({ refusal }: { refusal: ForecastRefusal }): ReactElement {
  const copy = COPY[refusal.code] ?? COPY.fit_failed
  const Icon = copy.icon
  const measured = refusal.have != null && refusal.need != null
  const pct = measured ? Math.min(100, Math.round((refusal.have! / refusal.need!) * 100)) : null

  return (
    <div className="rounded-xl border border-border bg-surface-2/40 p-5">
      <div className="flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-surface-2 text-foreground">
          <Icon className="size-4" />
        </div>
        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="t-title text-[0.95rem] text-foreground">{copy.title}</span>
            <Badge tone={copy.tone}>{copy.label}</Badge>
          </div>

          {measured ? (
            <div className="space-y-1.5">
              <div className="flex items-baseline gap-2">
                <span className="tabular font-mono text-[1.35rem] font-bold leading-none text-foreground">
                  {refusal.have}
                </span>
                <span className="tabular font-mono text-[0.72rem] text-muted-foreground">
                  of {refusal.need} observations needed
                </span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
                <div
                  className="h-full rounded-full bg-risk"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          ) : null}

          <p className="text-[0.78rem] leading-snug text-muted-foreground">{refusal.reason}</p>
        </div>
      </div>
    </div>
  )
}
