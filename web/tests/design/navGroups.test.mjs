/**
 * The shell draws each portal's section list twice, and the two must be one list.
 *
 * Below `lg` the fixed rail is hidden, so the navigation moved into a drawer.
 * That is the exact shape of change that produces a second, hardcoded copy of the
 * nav — and the drift would be invisible for a long time, because it would only
 * show on the portals whose lists differ most: `ai_team` has sixteen sections and
 * `devops` has nine, and nobody checks the phone layout of the sixteenth.
 *
 * `navGroupsFor` is therefore the only thing either renderer calls, and its only
 * input is `sectionsFor(portal)`. This asserts the property that makes that
 * safe — grouping adds nothing, drops nothing, and reorders nothing.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { navGroupsFor, navSectionIds, activeSectionFrom, DEFAULT_GROUP } from '../../src/components/layout/navGroups.ts'
import { PORTALS, ROLE_SECTIONS, sectionsFor } from '../../src/lib/portal.ts'

test('every portal navigates exactly the sections its role declares', () => {
  // A scan whose subject can silently empty out proves nothing when it passes.
  assert.equal(PORTALS.length, 5)

  for (const portal of PORTALS) {
    // Grouping *clusters* — a heading gathers its rows, so the flattened order is
    // not the catalogue's. What must never change is the membership: the drawer
    // and the rail both render this, so a section added or lost here is a section
    // one width of the console can reach and the other cannot.
    assert.deepEqual(
      [...navSectionIds(portal)].sort(),
      [...ROLE_SECTIONS[portal]].sort(),
      `${portal}'s navigation must be ROLE_SECTIONS itself, no more and no less`,
    )
    // And no duplicates: a clustering bug that emits a row twice still passes a
    // set comparison, so the count is checked against the catalogue's own length.
    assert.equal(navSectionIds(portal).length, sectionsFor(portal).length)
  }
})

test('within a heading, the catalogue order survives', () => {
  // Order is the information inside a group: ROLE_SECTIONS lists each portal's
  // sections in the order an operator works through them.
  for (const portal of PORTALS) {
    const catalogue = ROLE_SECTIONS[portal]
    for (const group of navGroupsFor(portal)) {
      const ids = group.items.map((item) => item.id)
      const expected = catalogue.filter((id) => ids.includes(id))
      assert.deepEqual(ids, expected, `${portal} / ${group.heading} came out re-sorted`)
    }
  }
})

test('a section that declares no group is placed, not dropped', () => {
  // Most of the catalogue carries no `group`, so the fallback heading is the
  // common path rather than an edge case: losing it loses most of the nav.
  const groups = navGroupsFor('devops')
  const fallback = groups.find((group) => group.heading === DEFAULT_GROUP)
  const ungrouped = sectionsFor('devops').filter((section) => section.group == null)
  assert.equal(fallback?.items.length ?? 0, ungrouped.length)
})

test('the active section is read off the portal route, and nothing else is', () => {
  assert.equal(activeSectionFrom('/app/devops/redteam'), 'redteam')
  // The portal home names no section; the nav must highlight nothing rather than
  // guessing, or every portal root would light its first row before it is open.
  assert.equal(activeSectionFrom('/app/devops'), '')
  assert.equal(activeSectionFrom('/login'), '')
})

test('a tenant-pinned principal is not offered the process-wide sections', () => {
  // `ai_team` mounts `cache`, and the seeded analyst is pinned to a tenant, so that
  // nav item led to a 403 every time it was clicked — `require_infra_reader` refuses
  // a pinned principal outright, and rightly: a cache hit rate is one number over
  // every tenant that shared the worker, and no filter makes it safe.
  const pinned = navSectionIds('ai_team', 1)
  assert.ok(!pinned.includes('cache'), 'a pinned principal must not be offered cache')

  // The gate is the tenant pin, NOT the role name: the same role arrives un-pinned as
  // platform staff and is entitled to read it. Keying on the role would take the
  // section away from the operator who can use it.
  const staff = navSectionIds('ai_team', null)
  assert.ok(staff.includes('cache'), 'un-pinned platform staff keep the section')

  // devops is platform staff by construction and must be untouched.
  assert.ok(navSectionIds('devops', null).includes('cache'))

  // Nothing else moved: dropping one section must not reorder or lose the rest.
  assert.deepEqual(
    pinned,
    staff.filter((id) => id !== 'cache'),
    'only the refused section is removed',
  )
})
