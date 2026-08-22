/**
 * The pure half of the portal navigation — what the nav *contains*, per portal.
 *
 * The shell renders the same section list twice: once as the fixed rail at `lg`
 * and up, once inside the drawer below it. That is exactly the arrangement where
 * a second hardcoded list appears — and the two portals whose lists differ most
 * (`ai_team` has sixteen sections, `devops` has nine) are the two where nobody
 * would notice the drift for a month.
 *
 * So the grouping lives here, framework-free, and both renderers call it. The
 * only input is `sectionsFor(portal)`, which reads `ROLE_SECTIONS` — there is no
 * other source of nav entries in the shell, and `portalNav.test.mjs` asserts it.
 */

import { sectionsFor, type Portal, type Section } from '@/lib/portal'

/** The heading a section sits under when it declares none of its own. */
export const DEFAULT_GROUP = 'Workspace'

/** One heading and the sections beneath it, in the order the catalogue lists them. */
export interface NavGroup {
  heading: string
  items: Section[]
}

/**
 * Collect a portal's sections under their group headings, first-seen order kept.
 *
 * Order is the information here: `ROLE_SECTIONS` lists each portal's sections in
 * the order an operator works through them, and re-sorting the headings
 * alphabetically would put "Workspace" after "Trust" on four of the five portals.
 *
 * @param portal - The fine role whose navigation is being drawn.
 * @param tenantId - The principal's tenant pin, or `null` for platform staff. Drops the
 *   sections a pinned principal is structurally refused (see `PLATFORM_ONLY_SECTIONS`),
 *   so the rail never offers a page that can only 403.
 * @returns The groups, in nav order, each with its sections in nav order.
 */
export function navGroupsFor(portal: Portal, tenantId?: number | null): NavGroup[] {
  const groups = new Map<string, Section[]>()
  for (const section of sectionsFor(portal, tenantId)) {
    const key = section.group ?? DEFAULT_GROUP
    const bucket = groups.get(key)
    if (bucket) bucket.push(section)
    else groups.set(key, [section])
  }
  return [...groups.entries()].map(([heading, items]) => ({ heading, items }))
}

/**
 * Every section a portal's navigation offers, flattened back out of the groups.
 *
 * The check that the drawer and the rail cannot disagree with the catalogue.
 * Grouping *clusters*, so this is not `ROLE_SECTIONS` in its own order — but it
 * is the same membership, with no entry added, dropped or repeated.
 */
export function navSectionIds(portal: Portal, tenantId?: number | null): string[] {
  return navGroupsFor(portal, tenantId).flatMap((group) =>
    group.items.map((item) => item.id),
  )
}

/**
 * Which section of a portal a pathname is on, or `''` off a section route.
 *
 * `/app/[portal]/[section]` — index 3. Both the rail and the drawer highlight the
 * active row, and the breadcrumb names it, so the split lives in one place rather
 * than being re-derived (differently) in three components.
 */
export function activeSectionFrom(pathname: string): string {
  return pathname.split('/')[3] ?? ''
}
