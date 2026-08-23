/**
 * What `?document=<id>` opens on the Jobs screen, and what the screen says when it
 * cannot open it.
 *
 * The alert a tenant gets is *"policy-4.pdf failed at the embed stage"*. Before this it
 * linked to the Jobs list — a table of every run this tenant has ever had, with the one
 * the sentence was about somewhere in it, possibly on the other side of a filter. That
 * is a link to the right building and no room number.
 *
 * The parameter names a **document**, not a job, because that is what the emitter knows:
 * `entity_ref` is `document:25` and a document is the stable thing — one document can be
 * ingested twice (a re-queue is a fresh workflow and a fresh `job_runs` row), and the
 * link written at 09:00 must still mean something at 17:00.
 *
 * Three outcomes, and the whole point of this module is that the third is not silence:
 *
 * - **`row`** — the queue has a run for that document. That row's ingest log opens.
 *   `hiddenByFilter` is set when the active cut of the queue ("Failed", "In flight")
 *   excludes it, which the screen resolves by widening the filter and saying it did —
 *   a deep link that lands on a list not containing its own target is the original
 *   defect wearing a query string.
 * - **`document`** — no run for it in this queue. That is a real state, not an error: a
 *   document uploaded but never ingested owns no `job_runs` row, and `GET /jobs` is
 *   capped so an old run may simply be off the page. The screen opens the document's
 *   own ingest record instead, which either shows the record or carries the server's
 *   own 404 sentence — *"No document 900 is visible to this caller."* — verbatim. Both
 *   are specific; neither is an empty screen.
 * - **`none`** — no parameter. Nothing is opened and nothing is said.
 *
 * The resolver is pure and takes the visibility predicate rather than importing the
 * filter, so `tests/jobs/jobsFocus.test.mjs` can drive every branch without a DOM.
 */

import type { JobRunRow } from '@/lib/api/jobs'

/** What the screen should open for the current `?document=` value. */
export type JobFocus =
  /** A run for that document is in the queue; open its log. */
  | { kind: 'row'; row: JobRunRow; documentId: number; hiddenByFilter: boolean }
  /** No run for it here; open the document's own ingest record and say so. */
  | { kind: 'document'; documentId: number }
  /** No deep link. */
  | { kind: 'none' }

/**
 * Resolve the focused document against the rows the queue actually loaded.
 *
 * @param rows - Every row `GET /jobs` returned, unfiltered.
 * @param documentId - The `?document=` value, or `null`.
 * @param visible - Whether a row survives the active filter.
 */
export function resolveJobFocus(
  rows: readonly JobRunRow[],
  documentId: number | null,
  visible: (row: JobRunRow) => boolean,
): JobFocus {
  if (documentId === null) return { kind: 'none' }
  // Newest first: a re-queued document has more than one run, and the alert that sent
  // the reader here is about the latest of them. `GET /jobs` already orders that way;
  // taking the first match rather than scanning for a "best" one keeps this agreeing
  // with what the reader sees at the top of the table.
  const row = rows.find((candidate) => candidate.document_id === documentId)
  if (row === undefined) return { kind: 'document', documentId }
  return { kind: 'row', row, documentId, hiddenByFilter: !visible(row) }
}

/**
 * The one sentence the screen prints above the queue when it was opened from a link.
 *
 * It is written for the person who just clicked an alert and needs to know, in one
 * glance, whether they are looking at the thing they were sent to. `widened` says the
 * screen changed the filter on their behalf — a state they can see in the chips and
 * would otherwise have to reverse-engineer.
 */
export function focusNote(focus: JobFocus, widened: boolean): string | null {
  if (focus.kind === 'none') return null
  if (focus.kind === 'document') {
    return (
      `No run in this queue is for document ${focus.documentId} — it may never have been ` +
      `ingested, or its run may be older than this page. Its ingest record is below.`
    )
  }
  const where = `job #${focus.row.id}, document ${focus.documentId}`
  return widened
    ? `Showing all jobs so ${where} is visible — the filter it sits outside was cleared.`
    : `Opened ${where} from an alert.`
}
