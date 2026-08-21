/**
 * The chartable shapes of the memory payloads — pure, so they stay SSR- and
 * test-safe and hold no recharts import.
 *
 * Memory is the one surface in this portal with a **genuine event stream**
 * (`GET /memory/writes` carries an `op` and a `ts` per row), so it is the one
 * place a series over time is a measurement rather than an invention. Everything
 * else here is a distribution over fields that already exist —
 * `MemoryFactRow.confidence`, `MemoryFactRow.importance`,
 * `MemoryRetentionResponse.at_risk` — none of which was rendered anywhere before.
 */

import type {
  MemoryFactRow,
  MemoryRetentionCounts,
  MemoryWriteRow,
} from '@/lib/api/memory'

/** The plain verb each write op is shown as. One spelling for the whole surface. */
export const OP_LABEL: Record<string, string> = {
  ADD: 'Learned',
  UPDATE: 'Updated',
  INVALIDATE: 'Retired',
  NOOP: 'Reviewed',
}

/** The write stream, bucketed by day and split by op. */
export interface WriteActivity {
  /** One row per day in the window: the label, a `total`, and a count per op key. */
  rows: Array<Record<string, string | number>>
  /** The op tallies over the window, **largest first**. */
  series: Array<{ key: string; label: string; value: number }>
  /** Writes that fell inside the window, for the receipt. */
  plotted: number
  /** Writes older than the drawn window — stated rather than piled on day one. */
  omitted: number
  /** The window the chart actually draws, in words ("21 Aug" / "08–21 Aug"). */
  window: string | null
}

/** The most trailing days the activity chart will ever draw. */
const WRITE_WINDOW_DAYS = 14

/** A day key (`2026-08-21`) in the reader's own timezone. */
function dayKey(iso: string): string | null {
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return null
  const y = t.getFullYear()
  const m = String(t.getMonth() + 1).padStart(2, '0')
  const d = String(t.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

/**
 * `2026-08-21` → `21 Aug`, in the reader's own locale.
 *
 * Built once: `Intl.DateTimeFormat` is the expensive half of a `toLocaleDateString`
 * call, and this runs per axis tick. The locale is left undefined on purpose — a
 * pinned `en-US` is a hardcoded date format wearing an Intl coat.
 */
const DAY_FORMAT = new Intl.DateTimeFormat(undefined, { day: '2-digit', month: 'short' })

function dayLabel(key: string): string {
  const [y, m, d] = key.split('-').map(Number)
  return DAY_FORMAT.format(new Date(y, m - 1, d))
}

/** Whole days between two `YYYY-MM-DD` keys, inclusive of both ends. */
function spanDays(from: string, to: string): number {
  const [fy, fm, fd] = from.split('-').map(Number)
  const [ty, tm, td] = to.split('-').map(Number)
  const ms = Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)
  return Math.floor(ms / 86_400_000) + 1
}

/**
 * What the agent did to its own memory, per day, over the days the write log
 * actually covers — at most the trailing {@link WRITE_WINDOW_DAYS}.
 *
 * **Empty days inside the record are drawn, not skipped.** A categorical axis
 * that lists only the days something happened compresses a quiet fortnight into
 * one step and reads as steady activity; the zero is part of the measurement.
 *
 * **Days before the first write are not drawn at all**, and that is the fix for a
 * real defect: a fixed 14-day window on a log whose every entry landed on one day
 * rendered thirteen empty columns and one spike at the right edge, which reads as
 * a broken chart rather than as a young record. There was no memory before the
 * first write, so those columns measured nothing. The window that *is* drawn is
 * named in {@link WriteActivity.window} and printed on the receipt, and writes
 * older than a full fortnight come back as `omitted` rather than being piled onto
 * the first bucket — the range is stated, never invented.
 *
 * Each row also carries a `total`, because sparse daily activity is a **count per
 * day** and the honest mark for that is a bar. The per-op tallies stay on
 * `series` for the legend beside it.
 */
export function writeActivity(
  rows: readonly MemoryWriteRow[],
  maxWindowDays: number = WRITE_WINDOW_DAYS,
): WriteActivity {
  const dated = rows
    .map((r) => ({ key: r.ts == null ? null : dayKey(r.ts), op: OP_LABEL[r.op] ?? String(r.op) }))
    .filter((r): r is { key: string; op: string } => r.key !== null)
  if (dated.length === 0) return { rows: [], series: [], plotted: 0, omitted: 0, window: null }

  const newest = dated.reduce((a, b) => (a.key > b.key ? a : b)).key
  const oldest = dated.reduce((a, b) => (a.key < b.key ? a : b)).key
  const windowDays = Math.max(1, Math.min(maxWindowDays, spanDays(oldest, newest)))

  const [ny, nm, nd] = newest.split('-').map(Number)
  const end = new Date(ny, nm - 1, nd)

  const days: string[] = []
  for (let i = windowDays - 1; i >= 0; i -= 1) {
    const day = new Date(end)
    day.setDate(end.getDate() - i)
    days.push(
      `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`,
    )
  }
  const inWindow = new Set(days)

  const perDay = new Map<string, Map<string, number>>(days.map((d) => [d, new Map()]))
  const totals = new Map<string, number>()
  let plotted = 0
  let omitted = 0
  for (const row of dated) {
    if (!inWindow.has(row.key)) {
      omitted += 1
      continue
    }
    plotted += 1
    const bucket = perDay.get(row.key) as Map<string, number>
    bucket.set(row.op, (bucket.get(row.op) ?? 0) + 1)
    totals.set(row.op, (totals.get(row.op) ?? 0) + 1)
  }

  const series = [...totals.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([label, value]) => ({ key: label, label, value }))

  const out = days.map((key) => {
    const bucket = perDay.get(key) as Map<string, number>
    const row: Record<string, string | number> = { day: dayLabel(key) }
    let total = 0
    for (const s of series) {
      const n = bucket.get(s.key) ?? 0
      row[s.key] = n
      total += n
    }
    row.total = total
    return row
  })

  const first = days[0]
  const last = days[days.length - 1]
  const window = first === last ? dayLabel(last) : `${dayLabel(first)} – ${dayLabel(last)}`

  return { rows: out, series, plotted, omitted, window }
}

/** One band of the confidence histogram. */
export interface ConfidenceBar {
  band: string
  facts: number
}

/** The five confidence bands, low → high. */
const BANDS = ['0–20%', '20–40%', '40–60%', '60–80%', '80–100%'] as const

/**
 * How sure the agent is of what it believes — facts per 20-point confidence band.
 *
 * Only currently-valid facts are counted: a superseded row's confidence is a
 * belief the agent has already abandoned, and mixing the two would draw a
 * distribution nothing in the store actually holds.
 */
export function confidenceBands(rows: readonly MemoryFactRow[]): ConfidenceBar[] {
  const counts = BANDS.map(() => 0)
  let any = false
  for (const row of rows) {
    if (!row.is_valid) continue
    any = true
    const c = Number.isFinite(row.confidence) ? Math.max(0, Math.min(1, row.confidence)) : 0
    counts[Math.min(BANDS.length - 1, Math.floor(c * BANDS.length))] += 1
  }
  return any ? BANDS.map((band, i) => ({ band, facts: counts[i] })) : []
}

/** The median importance weight across the valid facts, or `null` when there are none. */
export function medianImportance(rows: readonly MemoryFactRow[]): number | null {
  const values = rows
    .filter((r) => r.is_valid && Number.isFinite(r.importance))
    .map((r) => r.importance)
    .sort((a, b) => a - b)
  if (values.length === 0) return null
  const mid = Math.floor(values.length / 2)
  return values.length % 2 === 1 ? values[mid] : (values[mid - 1] + values[mid]) / 2
}

/** How many times the valid facts have been recalled, in total. */
export function totalRecalls(rows: readonly MemoryFactRow[]): number {
  return rows.reduce((sum, r) => sum + (r.is_valid && Number.isFinite(r.access_count) ? r.access_count : 0), 0)
}

/** One tier of the retention horizon. */
export interface RetentionBar {
  tier: string
  rows: number
}

/** What is sitting past the retention horizon right now, tier by tier. */
export function atRiskBars(counts: MemoryRetentionCounts): RetentionBar[] {
  return [
    { tier: 'Turns', rows: counts.messages },
    { tier: 'Sessions', rows: counts.sessions },
    { tier: 'Facts', rows: counts.facts },
    { tier: 'Queue', rows: counts.jobs },
  ]
}
