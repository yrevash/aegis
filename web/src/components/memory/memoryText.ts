/**
 * Pure, chart-free logic for the Memory surface (§4.3) — ported from the Vite
 * app. Everything that decides *what words and numbers appear* lives here so it
 * stays SSR/test-safe and free of recharts / force-graph imports.
 *
 * This is also where the surface's jargon → plain-language translation lives: the
 * honest tech (bitemporal store, relevance × recency × importance) stays in code
 * and tooltips; the executive-facing labels come from here.
 */

import type {
  MemoryFactRow,
  MemorySessionRow,
  MemoryWriteRow,
  RecallDebugItem,
} from '@/lib/api/memory'

/** How much context the assembled working-memory block is allowed to fill. */
export const TOKEN_BUDGET = 512

/** Map an age in days to a 0..1 freshness score (recent = high). */
export function recency(ageDays: number): number {
  return 1 / (1 + Math.max(0, ageDays) / 7)
}

/** Fraction (0..100) of the context budget a recall used — clamped + rounded. */
export function budgetPct(used: number, budget: number = TOKEN_BUDGET): number {
  if (budget <= 0) return 0
  return Math.min(100, Math.max(0, Math.round((used / budget) * 100)))
}

/** Total conversation turns across a subject's sessions. */
export function totalTurns(sessions: MemorySessionRow[]): number {
  return sessions.reduce((sum, s) => sum + (s.turn_count ?? 0), 0)
}

/** Count of facts the agent currently believes (valid ones). */
export function validFactCount(facts: MemoryFactRow[]): number {
  return facts.filter((f) => f.is_valid).length
}

/**
 * A cumulative "memory growth" series — how the count of held facts changed over
 * time — derived honestly from the write-log (ADD grows it, INVALIDATE retires
 * one). Chronological, clamped at zero. Fewer than two points ⇒ the hero trend
 * renders a quiet baseline rather than inventing a shape.
 */
export function factGrowthSeries(writes: MemoryWriteRow[]): number[] {
  const ordered = [...writes]
    .filter((w) => w.ts != null)
    .sort((a, b) => new Date(a.ts as string).getTime() - new Date(b.ts as string).getTime())
  let running = 0
  const series: number[] = []
  for (const w of ordered) {
    if (w.op === 'ADD') running += 1
    else if (w.op === 'INVALIDATE') running = Math.max(0, running - 1)
    series.push(running)
  }
  return series
}

/**
 * Plain-language status for a fact — replaces the "bitemporal / invalidated"
 * jargon on the primary surface. The honest belief-history detail stays one
 * layer down in the fact's expander.
 */
export function factStatus(fact: MemoryFactRow): { label: string; tone: 'ok' | 'muted' } {
  return fact.is_valid
    ? { label: 'Current', tone: 'ok' }
    : { label: 'Superseded', tone: 'muted' }
}

/** Humanise a snake_case profile key ("open_cases" → "open cases"). */
export function humanizeKey(key: string): string {
  return key.replace(/_/g, ' ').trim()
}

/** Render a scalar / array profile value to a compact display string. */
export function humanizeValue(value: unknown): string {
  if (value == null) return '—'
  if (Array.isArray(value)) {
    return value.map((v) => String(v).replace(/_/g, ' ')).join(', ')
  }
  if (typeof value === 'number') return value.toLocaleString('en-US')
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  return String(value).replace(/_/g, ' ')
}

export interface SummaryHighlight {
  label: string
  value: string
}

/**
 * Distil the structured profile into up-to-`max` plain highlights for the hero
 * ("Plan — Premium · Region — EU · Open cases — 2"). Skips empty values so the
 * line never invents anything the record doesn't hold (no-fakes bar).
 */
export function summaryHighlights(
  data: Record<string, unknown>,
  max = 3,
): SummaryHighlight[] {
  const out: SummaryHighlight[] = []
  for (const [key, raw] of Object.entries(data)) {
    if (out.length >= max) break
    if (raw == null) continue
    if (Array.isArray(raw) && raw.length === 0) continue
    if (typeof raw === 'string' && raw.trim() === '') continue
    out.push({ label: humanizeKey(key), value: humanizeValue(raw) })
  }
  return out
}

/** The three recall dimensions, in plain words (honest formula in the hints). */
export interface RecallDimension {
  key: 'match' | 'fresh' | 'weight'
  label: string
  hint: string
  hex: string
}

export const RECALL_DIMENSIONS: RecallDimension[] = [
  {
    key: 'match',
    label: 'Match',
    hint: 'How closely this fits the question (vector similarity).',
    hex: 'var(--blue-600)',
  },
  {
    key: 'fresh',
    label: 'Fresh',
    hint: 'How recent it is — newer memories score higher (recency).',
    hex: 'var(--ok-ink)',
  },
  {
    key: 'weight',
    label: 'Weight',
    hint: 'How important the memory is judged to be (importance).',
    hex: 'var(--blue-800)',
  },
]

/**
 * The three 0..1 sub-scores behind a recall decision, in plain terms. Together
 * they are "how it decided what to recall" — the honest composite is
 * relevance × recency × importance, surfaced visually rather than as a phrase.
 */
export function recallScores(item: RecallDebugItem): Record<RecallDimension['key'], number> {
  return {
    match: clamp01(item.score),
    fresh: clamp01(recency(item.age_days)),
    weight: clamp01(item.importance / 10),
  }
}

function clamp01(n: number): number {
  if (!Number.isFinite(n)) return 0
  return Math.max(0, Math.min(1, n))
}

/**
 * The query a recall trace should actually run, or `null` when there is none.
 *
 * The panel's own docstring says it refuses to seed a question — *"a placeholder
 * question would put words in the operator's mouth and trace a recall nobody asked
 * for"* — and three lines below it the submit handler did
 * `setQuery(draft.trim() || 'request status')`, substituting exactly such a question.
 * An empty box then traced "request status", and the ranked rows underneath were the
 * agent's honest answer to a question nobody had asked.
 *
 * `null` is the honest reading of an empty box, and the panel invites a question instead
 * of inventing one.
 *
 * @param draft - Whatever is in the input.
 * @returns The trimmed query, or `null` when the box is empty or only whitespace.
 */
export function recallQuery(draft: string): string | null {
  const trimmed = draft.trim()
  return trimmed === '' ? null : trimmed
}
