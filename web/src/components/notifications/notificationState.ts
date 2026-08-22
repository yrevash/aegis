/**
 * The bell's state, as a pure reducer — no React, no fetch, no DOM.
 *
 * The unread count is the one number on this feature anybody looks at, and it is drawn
 * on every screen in the product. It has three inputs that disagree by nature: a count
 * the server computed over the whole feed, a page of rows the limit truncated, and a
 * socket delivering rows the page has never seen. Getting it wrong is not a rendering
 * bug — a badge reading 3 over an empty panel, or 0 over an unread row, is the console
 * lying about whether anything needs attention.
 *
 * So the arithmetic lives here, where `tests/notifications/notificationState.test.mjs`
 * exercises it directly, and the components below only draw what it returns.
 *
 * Three rules the count follows, each because the naive version is wrong:
 *
 * - **`unread` is the server's, not `rows.filter(unread).length`.** `GET /notifications`
 *   returns a *limited* page and an *unlimited* count. Recomputing from the page would
 *   under-report the moment a tenant has more unread than the limit.
 * - **A re-delivered id is not a second notification.** A reconnect can replay the row
 *   that was in flight when the socket dropped, and at-least-once delivery is the normal
 *   guarantee for a stream like this. Arrival is keyed on `id`.
 * - **Reading is idempotent and floored at zero.** Two clicks on one row, or a row that
 *   arrived already-read, must not drive the badge negative.
 */

import type { Signal } from '@/config/signals'
import type { NotificationRow } from '@/lib/api/notifications'

/**
 * How many rows the panel keeps in memory.
 *
 * The stream runs for as long as the console is open, which on a demo machine is all
 * day; without a cap the array is unbounded. Older rows are not lost — they are on the
 * server, and the next `GET /notifications` re-reads them.
 */
export const MAX_ROWS = 60

/** What the bell knows. */
export interface NotificationState {
  /** Newest first. Never longer than {@link MAX_ROWS}. */
  rows: NotificationRow[]
  /** Unread across the whole feed. Not derived from {@link rows}. */
  unread: number
  /** Ids that arrived on the socket this session — what the live region announces. */
  arrived: string[]
}

/** The empty feed: what the bell shows before the first read, and after a sign-out. */
export const EMPTY_NOTIFICATIONS: NotificationState = { rows: [], unread: 0, arrived: [] }

/** Everything that can change the feed. */
export type NotificationAction =
  | { type: 'loaded'; rows: NotificationRow[]; unread: number }
  | { type: 'arrived'; row: NotificationRow }
  | { type: 'read'; id: string; at: string }
  | { type: 'readAll'; at: string }
  | { type: 'cleared' }

/** Newest first, by `created_at`; ties keep their relative order. */
function newestFirst(rows: NotificationRow[]): NotificationRow[] {
  return [...rows].sort(
    (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || 0,
  )
}

/** The state after one action. Pure: never mutates the state it is given. */
export function notificationReducer(
  state: NotificationState,
  action: NotificationAction,
): NotificationState {
  switch (action.type) {
    case 'loaded':
      return {
        rows: newestFirst(action.rows).slice(0, MAX_ROWS),
        unread: Math.max(0, action.unread),
        arrived: state.arrived,
      }

    case 'arrived': {
      const seen = state.rows.findIndex((row) => row.id === action.row.id)
      if (seen !== -1) {
        // A replay after a reconnect. Take the newer copy — its `read_at` may have
        // changed on another tab — but do not count it again.
        const rows = [...state.rows]
        rows[seen] = action.row
        return { ...state, rows }
      }
      return {
        rows: [action.row, ...state.rows].slice(0, MAX_ROWS),
        unread: state.unread + (action.row.read_at === null ? 1 : 0),
        // Only the tail matters — the live region announces the last one — and an
        // unbounded list on a socket that stays open all day is the same leak the row
        // cap exists to close.
        arrived: [...state.arrived, action.row.id].slice(-10),
      }
    }

    case 'read': {
      const target = state.rows.find((row) => row.id === action.id)
      // Unknown id, or already read: the badge does not move. This is the second
      // click on one row, and it must not be a decrement.
      if (target === undefined || target.read_at !== null) return state
      return {
        ...state,
        rows: state.rows.map((row) =>
          row.id === action.id ? { ...row, read_at: action.at } : row,
        ),
        unread: Math.max(0, state.unread - 1),
      }
    }

    case 'readAll':
      return {
        ...state,
        rows: state.rows.map((row) => (row.read_at === null ? { ...row, read_at: action.at } : row)),
        unread: 0,
      }

    case 'cleared':
      return EMPTY_NOTIFICATIONS

    default:
      return state
  }
}

/**
 * The reserved status tone for a severity.
 *
 * `info` is **neutral**, not blue: blue is the identity hue and means "this product",
 * so spending it on a severity would make every routine alert look like an action
 * (DESIGN.md §2). An unrecognised severity is neutral too — it is not an escalation.
 */
export function severityTone(severity: string): Signal {
  if (severity === 'critical') return 'block'
  if (severity === 'warning') return 'risk'
  return 'neutral'
}

/**
 * The word that goes with the mark.
 *
 * Status is a glyph, a word and a hue — all three (DESIGN.md §2/§4). The word is the
 * part that survives colour-blindness and a greyscale projector, so every severity has
 * one even when the row only shows the glyph and keeps the word for assistive tech.
 */
export function severityLabel(severity: string): string {
  if (severity === 'critical') return 'Critical'
  if (severity === 'warning') return 'Warning'
  if (severity === 'info') return 'Information'
  return severity === '' ? 'Notice' : severity
}

/**
 * A compact age: `now`, `4m`, `3h`, `2d`, then the date.
 *
 * Deliberately short — this sits at the end of a row in a 20rem panel, and "about 4
 * minutes ago" wraps the title. Anything older than a week is a date, because "9d"
 * stops being a fact anyone can place.
 *
 * @param iso - ISO 8601 timestamp.
 * @param now - Milliseconds since the epoch; injected so the test is not clock-dependent.
 */
export function relativeAge(iso: string, now: number): string {
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return ''
  const seconds = Math.max(0, Math.round((now - then) / 1000))
  if (seconds < 45) return 'now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${Math.max(1, minutes)}m`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.round(hours / 24)
  if (days <= 7) return `${days}d`
  return new Date(then).toISOString().slice(0, 10)
}

/**
 * What the badge shows. Above 99 it saturates, because a three-digit badge is wider
 * than the bell it sits on and the exact number stops being the point.
 */
export function badgeCount(unread: number): string {
  return unread > 99 ? '99+' : String(unread)
}

/**
 * What the bell's `aria-label` says.
 *
 * The count must not live only in the badge: a badge is a visual, and a screen-reader
 * user gets the number from here or not at all.
 */
export function bellLabel(unread: number): string {
  if (unread <= 0) return 'Alerts, none unread'
  return `Alerts, ${unread} unread`
}

/**
 * The one line the panel says about its live socket.
 *
 * Stated, never hidden: a bell that has silently stopped receiving is worse than no
 * bell, because it reads as "nothing has happened".
 */
export function streamNote(status: 'connecting' | 'live' | 'retrying' | 'closed'): string {
  if (status === 'live') return 'Live'
  if (status === 'connecting') return 'Connecting'
  if (status === 'retrying') return 'Reconnecting'
  return 'Not connected'
}
