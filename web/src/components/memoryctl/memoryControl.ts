/**
 * The words and numbers the memory control plane puts on screen, as pure functions.
 *
 * Everything here decides *what a person is told* about an irreversible action or an
 * automatic one, which is the part of this surface most worth testing and the part most
 * easily got wrong by a template. Kept free of React and of the api layer so
 * `web/tests/memoryctl/memoryControl.test.mjs` can drive it directly.
 *
 * Two rules run through all of it:
 *
 * 1. **A destructive action names what it removes, in counts, before it is pressed.**
 *    "Delete everything?" is not consent; "this deletes 12 facts, 340 turns and 8
 *    sessions, permanently" is.
 * 2. **Never claim a number the backend did not give.** A count that is not loaded yet
 *    is absent from the sentence rather than rendered as zero, because "0 facts" and
 *    "we have not looked yet" are different statements and only one of them is safe to
 *    act on.
 */

import type {
  MemoryForgetResponse,
  MemoryRetentionCounts,
  MemorySubjectRow,
} from '@/lib/api/memory'

/** English pluralisation for the one irregular shape this surface needs. */
function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

/**
 * The clause listing the tiers a retention pass would touch, or `null` for none.
 *
 * `null` rather than "0 rows" is deliberate: the empty case is not a smaller version of
 * the full one, it is a different sentence ("nothing is past the horizon yet"), and a
 * caller that cannot tell them apart writes the wrong one.
 */
export function retentionClause(counts: MemoryRetentionCounts | null): string | null {
  if (counts === null) return null
  const parts: string[] = []
  if (counts.messages > 0) parts.push(plural(counts.messages, 'turn', 'turns'))
  if (counts.sessions > 0) parts.push(plural(counts.sessions, 'session', 'sessions'))
  if (counts.facts > 0) parts.push(plural(counts.facts, 'superseded fact', 'superseded facts'))
  if (counts.jobs > 0) parts.push(plural(counts.jobs, 'queue row', 'queue rows'))
  if (parts.length === 0) return null
  if (parts.length === 1) return parts[0]
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}

/**
 * What the erase button promises to remove, said before it is pressed.
 *
 * Takes the counts the screen has already read rather than the erasure's own response,
 * so the promise and the receipt are two different measurements of the same thing and a
 * mismatch between them is visible instead of hidden by reusing one number twice.
 */
export function erasureWarning(row: MemorySubjectRow | null): string {
  if (row === null) return 'Choose a subject first.'
  const parts: string[] = []
  if (row.fact_count > 0) parts.push(plural(row.fact_count, 'fact', 'facts'))
  if (row.session_count > 0) parts.push(plural(row.session_count, 'session', 'sessions'))
  const what = parts.length > 0 ? `${parts.join(' and ')}, plus every stored turn,` : 'every stored row'
  return `This deletes ${what} the profile and the write log for ${row.label}. The rows are removed from the database, not hidden, and it cannot be undone.`
}

/** The receipt an erasure hands back — what actually went, tier by tier. */
export function erasureReceipt(result: MemoryForgetResponse): string {
  const parts = [
    plural(result.deleted_facts, 'fact', 'facts'),
    plural(result.deleted_messages, 'turn', 'turns'),
    plural(result.deleted_sessions, 'session', 'sessions'),
    plural(result.deleted_profiles, 'profile', 'profiles'),
    plural(result.deleted_writes, 'write-log row', 'write-log rows'),
  ]
  return `Removed ${parts.join(', ')}.`
}

/**
 * How the input rail changed a written fact on its way in, or `null` when it did not.
 *
 * A redaction that happens silently is a redaction the operator will later mistake for
 * the agent forgetting something. This is the sentence that stops that.
 */
export function screeningNote(verdict: string, redactions: string[]): string | null {
  if (verdict === 'redact') {
    const kinds = redactions.length > 0 ? ` (${redactions.join(', ')})` : ''
    return `The guardrails redacted personal data${kinds} before storing this. The agent will recall the redacted text.`
  }
  if (verdict === 'flag') {
    return 'The guardrails flagged this as off-topic but stored it. It will be recalled as written.'
  }
  return null
}

/** A subject's one-line size, for the picker. */
export function subjectSummary(row: MemorySubjectRow): string {
  if (row.fact_count === 0 && row.session_count === 0) return 'Nothing stored yet'
  return `${plural(row.fact_count, 'fact', 'facts')} · ${plural(row.session_count, 'session', 'sessions')}`
}

/**
 * Where the retention horizon's value came from, in the badge language §7.4 settled on.
 *
 * A control that shows a number without saying whether it is the platform's default or
 * this tenant's own choice is a control nobody can reason about — that is the whole
 * lesson of `agent.gate_min_risk`, and it applies to a value that is merely *displayed*
 * exactly as much as to one that is edited.
 */
export function sourceLabel(source: string): string {
  if (source === 'tenant') return "your tenant's"
  if (source === 'user') return 'your choice'
  return 'Aegis default'
}
