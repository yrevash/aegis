/**
 * The audit filter, as the server understands it.
 *
 * §7.11. The console used to fetch a page of the trail and narrow it in the browser, so
 * every control answered a question about the page rather than about the trail: "what
 * did this actor do last Tuesday" returned nothing whenever last Tuesday had fallen off
 * the end. These fields become `GET /audit` query parameters, and the database does the
 * narrowing.
 *
 * `tenantId` is deliberately here and deliberately optional: it is a *selector* the
 * server grants only to a platform admin, and a tenant-bound caller naming any other
 * tenant is refused with a 403 whether that tenant exists or not. The client never
 * decides scope; it only asks.
 */

import type { AuditOutcome } from '@/lib/api/types'

/** An active audit filter. Every field empty means "the whole trail I may read". */
export interface AuditQuery {
  /** Exact actor. */
  actor: string
  /** Action family — `ops.`, `tool:`, `guardrail.` — matched as a prefix. */
  actionPrefix: string
  /** Exact model deployment. */
  model: string
  /** Blocked / completed, or `null` for both. */
  outcome: AuditOutcome | null
  /** Free text across action, actor, model, trace and approver. */
  text: string
  /** Inclusive lower bound, as a `datetime-local` value (`YYYY-MM-DDTHH:mm`). */
  since: string
  /** Inclusive upper bound, same shape. */
  until: string
  /** Platform-admin tenant selector; `null` means "every tenant I may read". */
  tenantId: number | null
  /** Rows per page, clamped by the server to `[1, 200]`. */
  limit: number
}

/** The default page size — the server's own default, restated so the URL is explicit. */
export const DEFAULT_AUDIT_LIMIT = 50

/** An empty filter: no predicate beyond the caller's own sealed scope. */
export const EMPTY_AUDIT_QUERY: AuditQuery = {
  actor: '',
  actionPrefix: '',
  model: '',
  outcome: null,
  text: '',
  since: '',
  until: '',
  tenantId: null,
  limit: DEFAULT_AUDIT_LIMIT,
}

/** Whether anything beyond the page size is set — drives the "clear filters" affordance. */
export function isFiltered(q: AuditQuery): boolean {
  return (
    q.actor.trim() !== '' ||
    q.actionPrefix.trim() !== '' ||
    q.model.trim() !== '' ||
    q.outcome !== null ||
    q.text.trim() !== '' ||
    q.since !== '' ||
    q.until !== '' ||
    q.tenantId !== null
  )
}

/**
 * A `datetime-local` value is wall-clock in the reader's zone; the API compares against
 * UTC. Converting through `Date` is the only correct reading of what the operator typed:
 * "since 09:00" means 09:00 where they are standing.
 *
 * Returns `null` for an empty or unparseable value, so a half-typed date filters nothing
 * rather than filtering everything out.
 */
export function localToIso(value: string): string | null {
  if (value.trim() === '') return null
  const ms = new Date(value).getTime()
  return Number.isFinite(ms) ? new Date(ms).toISOString() : null
}

/** Build the `GET /audit` query string (leading `?`, or empty when nothing is set). */
export function auditQueryString(q: AuditQuery): string {
  const params = new URLSearchParams()
  params.set('limit', String(q.limit))
  if (q.tenantId !== null) params.set('tenant_id', String(q.tenantId))
  if (q.actor.trim() !== '') params.set('actor', q.actor.trim())
  if (q.actionPrefix.trim() !== '') params.set('action_prefix', q.actionPrefix.trim())
  if (q.model.trim() !== '') params.set('model', q.model.trim())
  if (q.outcome !== null) params.set('outcome', q.outcome)
  if (q.text.trim() !== '') params.set('q', q.text.trim())
  const since = localToIso(q.since)
  if (since !== null) params.set('since', since)
  const until = localToIso(q.until)
  if (until !== null) params.set('until', until)
  return `?${params.toString()}`
}

/**
 * What to tell a reader who is looking at nothing.
 *
 * An empty result set is an instruction, not a shrug: when a filter is on, the answer is
 * "these filters match nothing" and the next move is to widen them; when no filter is on,
 * the trail really is empty and there is nothing to widen.
 */
export function emptyStateFor(q: AuditQuery): { title: string; hint: string } {
  return isFiltered(q)
    ? {
        title: 'No events match those filters',
        hint: 'Widen the time range, clear the actor, or search a shorter phrase.',
      }
    : {
        title: 'Nothing audited yet',
        hint: 'Recorded actions appear here, newest first — each with its actor, model and trace.',
      }
}

/**
 * The filters `GET /reports/audit.csv` accepts, taken from the live screen filter.
 *
 * The export is the streamed, keyset-paged, audited one from §7.12 — it has no `limit`,
 * so it is the whole filtered trail rather than the page in view. What it does *not*
 * take is the rest of this form: `model`, `trace_id`, `outcome`, the free text, and the
 * platform admin's tenant selector (the route resolves the scope from the token alone).
 * Those are named by {@link unexportableFilters} and said out loud on the screen, because
 * a file that silently contains more than the table it was downloaded from is evidence
 * of the wrong thing — which is the exact failure §7.12 exists to prevent.
 */
export function exportFilters(q: AuditQuery): {
  since?: string
  until?: string
  actor?: string
  actionPrefix?: string
} {
  const since = localToIso(q.since)
  const until = localToIso(q.until)
  return {
    ...(since !== null ? { since } : {}),
    ...(until !== null ? { until } : {}),
    ...(q.actor.trim() !== '' ? { actor: q.actor.trim() } : {}),
    ...(q.actionPrefix.trim() !== '' ? { actionPrefix: q.actionPrefix.trim() } : {}),
  }
}

/**
 * The active filters the CSV export cannot carry, named for the reader.
 *
 * Empty when the file will match the table. Non-empty means the download will contain
 * *more* rows than the screen, and the operator is told which control was dropped rather
 * than discovering it in a spreadsheet.
 */
export function unexportableFilters(q: AuditQuery): string[] {
  const dropped: string[] = []
  if (q.outcome !== null) dropped.push('outcome')
  if (q.model.trim() !== '') dropped.push('model')
  if (q.text.trim() !== '') dropped.push('search text')
  if (q.tenantId !== null) dropped.push('tenant')
  return dropped
}
