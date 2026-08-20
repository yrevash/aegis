'use client'

import { Brain, Clock } from 'lucide-react'
import type { ReactElement } from 'react'

import { CountUp, MiniTrend } from '@/components/shared'
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
  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-start gap-2">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-blue-200/12">
          <Brain className="size-4 text-blue-700" />
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
          <h2 className="t-title mt-0.5 truncate text-foreground">{subjectLabel}</h2>
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
                <Clock className="size-3" /> last seen {lastSeen}
              </span>
            )}
          </span>
        )}
      </div>

      {trend && trend.length > 1 && (
        <div className="-mt-1">
          <div className="mb-1 flex items-center gap-1.5">
            <span className="eyebrow text-[0.56rem]">Memory growth</span>
            <InfoTip label="About memory growth">
              Facts the agent has recorded about this subject over time.
            </InfoTip>
          </div>
          <MiniTrend data={trend} color="agent" height={40} variant="area" />
        </div>
      )}

      {/* The three headline counts — figures lead (§1.1), count-up on mount. */}
      <dl className="mt-auto grid grid-cols-3 gap-3 border-t border-border/70 pt-3">
        {stats.map((s) => (
          <div key={s.label} className="flex flex-col gap-0.5">
            <dt className="eyebrow text-[0.58rem]">{s.label}</dt>
            <dd className={cn('t-metric text-foreground', s.value == null && 'text-muted-foreground')}>
              {s.value == null ? '—' : <CountUp value={s.value} />}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
