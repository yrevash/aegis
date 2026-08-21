'use client'

import { Brain, Clock } from 'lucide-react'
import type { ReactElement } from 'react'

import { CountUp, MiniTrend } from '@/components/shared'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'

import type { SummaryHighlight } from './memoryText'

interface Stat {
  label: string
  value: number | null
}

interface SubjectSummaryProps {
  /** The subject id this record belongs to, shown as the header. */
  subjectLabel: string
  /** Up-to-3 plain highlights distilled from the profile. */
  highlights: SummaryHighlight[]
  /** "3d ago" style last-activity label, or null when unknown. */
  lastSeen: string | null
  /** The three count-up stats: Facts · Sessions · Turns. */
  stats: Stat[]
  /** Optional memory-growth series (cumulative facts over time). */
  trend?: number[]
  /** True while the profile is still loading (shows a quiet placeholder line). */
  loading?: boolean
  /** Marks the demo subject's data as illustrative (kept honest). */
  sample?: boolean
}

/**
 * The Memory hero (§4.3): who the subject is, a short plain-language summary of
 * what the agent knows, and the three headline counts that count up on mount.
 */
export function SubjectSummary({
  subjectLabel,
  highlights,
  lastSeen,
  stats,
  trend,
  loading = false,
  sample = false,
}: SubjectSummaryProps): ReactElement {
  // A trend needs two points *and* two distinct values before it is a shape.
  const plottable = trend != null && trend.length > 1
  const hasShape = plottable && new Set(trend).size > 1
  const isFlat = plottable && !hasShape

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-2">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-blue-200/12">
          <Brain className="size-4 text-blue-700" aria-hidden />
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="eyebrow">Subject</span>
            <InfoTip label="About Aegis Memory">
              Aegis Memory — the agent&apos;s long-term memory of this subject. Facts, past
              sessions and a consolidated profile, stored in Postgres + pgvector.
            </InfoTip>
            {sample && (
              <span className="eyebrow rounded-sm border border-border/70 px-1.5 py-0.5 text-[0.56rem] text-muted-foreground">
                sample
              </span>
            )}
          </div>
          <h2 translate="no" className="t-title mt-0.5 truncate text-foreground">
            {subjectLabel}
          </h2>
        </div>
      </div>

      {/* Plain-language "what we know" line — never a paragraph. */}
      <div className="min-h-[1.5rem] text-sm leading-relaxed">
        {loading ? (
          <span className="text-muted-foreground">Building a profile…</span>
        ) : highlights.length === 0 && lastSeen == null ? (
          <span className="text-muted-foreground">No profile consolidated for this subject yet.</span>
        ) : (
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-foreground">
            {highlights.map((h, i) => (
              <span key={h.label} className="inline-flex items-center gap-2">
                {i > 0 && <span aria-hidden className="text-muted-foreground/50">·</span>}
                <span>
                  <span className="text-muted-foreground">{h.label} </span>
                  <span className="font-medium">{h.value}</span>
                </span>
              </span>
            ))}
            {lastSeen && (
              <span className="inline-flex items-center gap-1 text-muted-foreground">
                {highlights.length > 0 && <span aria-hidden className="text-muted-foreground/50">·</span>}
                <Clock className="size-3" aria-hidden /> last seen {lastSeen}
              </span>
            )}
          </span>
        )}
      </div>

      {/*
        **The label only appears when there is a shape under it.**

        `MiniTrend` draws a flat dashed baseline for a series it cannot plot, so a
        record whose fact count never moved rendered the words MEMORY GROWTH above
        40px of nothing — a heading for an absence, which is the one thing a label
        must never be. A series with no variance is not a small trend, it is a
        different statement, and it gets one line in the slot the chart would have
        taken (DESIGN.md §1). No writes at all says nothing here: the write-log
        card beside this one already carries that state.
      */}
      {hasShape ? (
        <div className="-mt-1">
          <div className="mb-1 flex items-center gap-1.5">
            <span className="eyebrow text-[0.56rem]">Memory growth</span>
            <InfoTip label="About memory growth">
              Facts the agent has recorded about this subject over time.
            </InfoTip>
          </div>
          <MiniTrend data={trend} color="agent" height={40} variant="area" />
        </div>
      ) : isFlat ? (
        <p className="-mt-1 text-xs text-muted-foreground">
          Fact count unchanged across the write log — nothing to plot.
        </p>
      ) : null}

      {/*
        The three counts this card owns. They are set at `stat`, not at the 28px
        metric step they used to wear: the screen's KPI band above is its one
        `display` group (DESIGN.md §3 allows exactly one per screen), and a second
        row of 28px numerals lower down competes with it instead of supporting it.
      */}
      <dl className="mt-1 grid grid-cols-3 gap-3 border-t border-border/70 pt-3">
        {stats.map((s) => (
          <div key={s.label} className="flex min-w-0 flex-col gap-0.5">
            <dt className="eyebrow text-[0.58rem]">{s.label}</dt>
            <dd>
              <Figure size="stat" className={cn(s.value == null && 'text-muted-foreground')}>
                {s.value == null ? '—' : <CountUp value={s.value} />}
              </Figure>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
