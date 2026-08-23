/**
 * Where an alert goes when its reader clicks it — resolved against *that reader's*
 * portal, not against the one that emitted it.
 *
 * ## The defect this replaces
 *
 * The backend used to write an absolute `href`: `/app/tenant_admin/jobs`. One portal is
 * named, and the row is read by up to five. A tenant-scoped notification reaches that
 * tenant's `tenant_admin`, `ai_team` **and** `client`, and platform staff (`admin`,
 * `devops`) receive every tenant's — so for four readers out of five the link named a
 * portal their session may not enter. The console's route guard does not raise on that;
 * it redirects the session home. Silently. Clicking an alert therefore marked it read
 * and put you back on your own dashboard with nothing said, which reads as a broken
 * button and is really a link written for somebody else.
 *
 * ## The contract now
 *
 * `href` keeps its name and its place on the wire — the field is consumed, the bell
 * ships, the generated schema agrees — and changes only its **value**. The emitter
 * writes the *section* that shows the entity and the *entity*, and nothing about who is
 * looking:
 *
 * ```
 * jobs?document=25              the ingest of document 25
 * approvals?approval=<id>       that gate
 * governance                    a screen with no entity
 * ```
 *
 * This module supplies the missing half: the viewer's own portal. It is the only place
 * a `/app/…` path is built from a notification, and it answers in three states rather
 * than two, because "nowhere to go" is a real answer that has to be *said*:
 *
 * - `link` — the viewer's portal mounts that section; here is the href.
 * - `elsewhere` — the target is a real section, but not one on this portal (a `client`
 *   has no Jobs). The row still renders and still marks read; it renders **without a
 *   link**, and the bell says which screen it would have been. Rendering the link
 *   anyway is precisely the bounce being fixed.
 * - `none` — the alert carries no target, or one no portal mounts.
 *
 * ## Why the section is checked against `portal.ts` and not against a table here
 *
 * `ROLE_SECTIONS` is already the single source of truth for what a portal mounts — the
 * nav is drawn from it and `app/[role]/[section]/page.tsx` 404s on anything outside it.
 * A second list in this file would be the "client-side mapping table that silently
 * rots" the backend's own comment warns about, and its first divergence would be
 * invisible: a link that looks right and 404s.
 *
 * @see backend/src/app/data/notifications.py — the field's contract, server side.
 */

import { isValidSection, SECTIONS, type Portal } from './portal'

/** What a notification's `href` resolves to for one particular reader. */
export type NotificationTarget =
  /** The viewer's portal mounts this section — navigate here. */
  | { kind: 'link'; href: string; section: string }
  /**
   * A real section, but not on this portal. `label` is its name on the portals that do
   * have it, so the reader is told what they are missing rather than shown a dead link.
   */
  | { kind: 'elsewhere'; section: string; label: string }
  /** No target at all: the alert names no screen, or names one that does not exist. */
  | { kind: 'none' }

/** The `none` answer, shared so callers can compare identity in tests if they like. */
const NOWHERE: NotificationTarget = { kind: 'none' }

/**
 * Split a stored target into its section slug and its query string.
 *
 * Accepts the portal-relative form (`jobs?document=25`) and, deliberately, the legacy
 * absolute one (`/app/tenant_admin/jobs`): rows written before this change are still in
 * the table, and a feature that made every existing alert unclickable would have traded
 * one silent dead end for another. The stale portal segment is dropped, which is exactly
 * the correction — it was never the reader's portal in the first place.
 *
 * Anything else — an external URL, an absolute path outside `/app`, an empty string —
 * is refused rather than guessed at. A notification is not an open redirect.
 */
function split(href: string): { section: string; query: string } | null {
  const raw = href.trim()
  if (raw === '') return null
  // An absolute or protocol-relative URL is never an in-app target.
  if (raw.includes('://') || raw.startsWith('//')) return null

  let rest = raw
  if (rest.startsWith('/')) {
    const parts = rest.split('/').filter((p) => p !== '')
    // Legacy: /app/<portal>/<section>[?query]
    if (parts.length < 3 || parts[0] !== 'app') return null
    rest = parts.slice(2).join('/')
  }

  const cut = rest.indexOf('?')
  const section = (cut === -1 ? rest : rest.slice(0, cut)).replace(/\/+$/, '')
  const query = cut === -1 ? '' : rest.slice(cut + 1)
  if (section === '' || section.includes('/')) return null
  return { section, query }
}

/**
 * Resolve one alert's `href` for one viewer.
 *
 * @param href - The stored target, or `null` for an alert that names no screen.
 * @param portal - The **viewer's** portal (`session.fineRole`), or `null` before the
 *   session has hydrated — in which case nothing is linked, because a link built
 *   against a guessed portal is the bug this module exists to remove.
 */
export function resolveNotificationTarget(
  href: string | null | undefined,
  portal: Portal | null | undefined,
): NotificationTarget {
  if (href == null) return NOWHERE
  const parts = split(href)
  if (parts === null) return NOWHERE
  // A slug no portal mounts is a target that does not exist — not one the viewer is
  // merely missing. Saying "it is on another portal" would be an invention.
  if (SECTIONS[parts.section] === undefined) return NOWHERE
  if (portal == null || !isValidSection(portal, parts.section)) {
    return {
      kind: 'elsewhere',
      section: parts.section,
      label: SECTIONS[parts.section].label,
    }
  }
  const query = parts.query === '' ? '' : `?${parts.query}`
  return {
    kind: 'link',
    href: `/app/${portal}/${parts.section}${query}`,
    section: parts.section,
  }
}
