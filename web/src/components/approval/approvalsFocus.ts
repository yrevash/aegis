/**
 * What `?approval=<id>` opens on the Approvals screen, and what it says when the gate
 * is not there.
 *
 * The alert is *"deactivate_account is waiting on a decision"*. It used to link to the
 * inbox, whose default cut is **Waiting, last 7 days** — so a gate decided in the ten
 * minutes it took someone to click, or raised nine days ago, landed the reader on a
 * queue that did not contain it. On a good day that reads as "somebody dealt with it";
 * on a bad day the reader assumes the alert was noise. Neither is something the screen
 * said, and that is the defect: an inbox showing nothing is not an answer to "where is
 * the gate I was told about".
 *
 * So a deep link is resolved against the rows and answered in four states:
 *
 * - **`waiting`** — still open. The card is scrolled to and marked as the one that was
 *   linked; the decision controls are the ones already there.
 * - **`decided`** — someone (or the SLA sweeper) got there first. The history row is
 *   opened rather than the screen pretending the queue is empty, and the note names the
 *   outcome, because "already approved" and "already rejected" are opposite facts.
 * - **`searching`** — the query has not been widened yet. Transient, one render.
 * - **`missing`** — widened to every status, every tenant, since the beginning, and
 *   still nothing. The note says what that means without inventing which of the reasons
 *   it was: the server narrows this list to what the caller may see, so "no such gate"
 *   and "not yours" are deliberately one answer, exactly as they are on `GET
 *   /documents/{id}/ingest`.
 *
 * Pure, so `tests/approval/approvalsFocus.test.mjs` drives all four without a DOM.
 */

import type { ApprovalInboxRow } from '@/lib/api/approvals'

/** Statuses that mean nothing further will happen to the gate. */
const CLOSED = new Set(['approved', 'rejected', 'expired'])

/** What the screen should open for the current `?approval=` value. */
export type ApprovalFocus =
  /** No deep link. */
  | { kind: 'none' }
  /** Linked, and the query has not yet been widened enough to say. */
  | { kind: 'searching'; id: string }
  /** Still open — the card with the decision on it. */
  | { kind: 'waiting'; row: ApprovalInboxRow }
  /** Already decided — the history row, and which way it went. */
  | { kind: 'decided'; row: ApprovalInboxRow }
  /** Not in the widest read this caller can make. */
  | { kind: 'missing'; id: string }

/**
 * Resolve the linked gate against the rows the inbox loaded.
 *
 * @param rows - What `GET /approvals` returned for the current query.
 * @param id - The `?approval=` value, or `null`.
 * @param widest - Whether the current query is already every status, every tenant,
 *   since the beginning. Until it is, a miss means "keep looking", not "not there".
 */
export function resolveApprovalFocus(
  rows: readonly ApprovalInboxRow[],
  id: string | null,
  widest: boolean,
): ApprovalFocus {
  if (id === null || id === '') return { kind: 'none' }
  const row = rows.find((candidate) => candidate.id === id)
  if (row === undefined) return widest ? { kind: 'missing', id } : { kind: 'searching', id }
  return CLOSED.has(row.status) ? { kind: 'decided', row } : { kind: 'waiting', row }
}

/**
 * The one sentence above the queue when it was opened from a link.
 *
 * `widened` says the screen changed the reader's filters to find the gate — which it
 * does silently otherwise, and a control that moves on its own is worse than the
 * missing row it was trying to fix.
 */
export function approvalFocusNote(focus: ApprovalFocus, widened: boolean): string | null {
  switch (focus.kind) {
    case 'none':
      return null
    case 'searching':
      return 'Looking for that gate across every status and window…'
    case 'waiting':
      return widened
        ? `Widened to find ${focus.row.action} — it is still waiting on a decision, below.`
        : `Opened ${focus.row.action}, which is waiting on a decision.`
    case 'decided': {
      const by = focus.row.decided_by ? ` by ${focus.row.decided_by}` : ' on its SLA deadline'
      return (
        `That gate was already ${focus.row.status}${focus.row.status === 'expired' ? '' : by}. ` +
        `Nothing is waiting on you for it — its record is open in the decision history.`
      )
    }
    case 'missing':
      return (
        `No gate ${focus.id} is visible to this account, across every status and since ` +
        `the beginning. It belongs to another tenant, or there is no such gate.`
      )
  }
}
