'use client'

import { Check, Minus, ShieldCheck, Users } from 'lucide-react'
import type { ReactElement } from 'react'

import { DataPanel } from '@/components/ui/DataPanel'
import { Badge } from '@/components/ui/Badge'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import type { AdminUser } from '@/lib/api/types'
import { PORTALS, ROLE_SECTIONS, SECTIONS, isValidSection, type Portal } from '@/lib/portal'
import { cn } from '@/lib/utils'

import { normalizeRole } from './roleCatalog'

/**
 * One band of authority: what it lets a person do, and the sections that carry it.
 *
 * The sections are ids from `SECTIONS`, so a band cannot name a surface that does not
 * exist — {@link AUTHORITY} is asserted against the catalogue below, and a rename in
 * `lib/portal.ts` fails that assertion instead of quietly drawing an empty row.
 */
interface Authority {
  id: string
  /** What this band of authority *is*, in the words a non-engineer would use. */
  label: string
  /** The one line that says where it stops. */
  detail: string
  /** Section ids that carry it. A portal holds the band if it reaches any of them. */
  sections: string[]
}

/**
 * The bands the console's own routing draws, in descending order of consequence.
 *
 * These are not a description of the permission model — they *are* it, read out of
 * `ROLE_SECTIONS`, the same catalogue the navigation renders and
 * `backend/tests/api/test_route_coverage.py` asserts every entry of against a live
 * route. Nothing on this map is a claim about access; every cell is the answer to
 * "would this portal's navigation carry that section", computed here.
 */
const AUTHORITY: Authority[] = [
  {
    id: 'delegation',
    label: 'Delegate access',
    detail: 'Grant, revoke and narrow what everybody else holds.',
    sections: ['roles'],
  },
  {
    id: 'money',
    label: 'Set what it may spend',
    detail: 'Tenant and per-user caps, and the ledger they are enforced against.',
    sections: ['governance'],
  },
  {
    id: 'gate',
    label: 'Decide the human gate',
    detail: 'Approve or reject an action the agent parked rather than took.',
    sections: ['approvals'],
  },
  {
    id: 'audit',
    label: 'Read the audit trail',
    detail: 'The append-only record of every action, actor and approver.',
    sections: ['audit'],
  },
  {
    id: 'stores',
    label: 'Reach the data stores',
    detail: 'The database browser and the MCP tool servers — platform ground.',
    sections: ['database', 'mcp'],
  },
  {
    id: 'build',
    label: 'Build and tune the agent',
    detail: 'Harness, evals, retrieval, memory, the graph and the guardrail policy.',
    sections: ['harness', 'mlops', 'evals', 'rag', 'graph', 'guardrails'],
  },
  {
    id: 'operate',
    label: 'Run the stack',
    detail: 'Versions, patches, the security posture and its framework map, the red team and the latency budget.',
    sections: ['stack', 'patch', 'security', 'compliance', 'redteam', 'latency'],
  },
  {
    id: 'agent',
    label: 'Talk to the agent',
    detail: 'The live console — where a question becomes a run.',
    sections: ['console'],
  },
  {
    id: 'outcomes',
    label: 'See the outcomes',
    detail: 'Savings against the frontier baseline, and the risk map.',
    sections: ['savings', 'risk'],
  },
]

/** Short column heads. The rail spells each of these out in full. */
const PORTAL_SHORT: Record<Portal, string> = {
  platform_admin: 'Platform',
  tenant_admin: 'Tenant',
  ai_team: 'AI team',
  devops: 'DevOps',
  client: 'Client',
}

/** What each portal is for, one line, in the header's tooltip rather than on the page. */
const PORTAL_PURPOSE: Record<Portal, string> = {
  platform_admin: 'Operates Aegis itself — every tenant, pinned to none.',
  tenant_admin: 'Administers exactly one customer tenant, and cannot see another.',
  ai_team: 'Builds and tunes the agent: the loop, the retrieval and the evals.',
  devops: 'Runs the stack — versions, patches, security and the latency budget.',
  client: 'The tenant end-user: their own outcomes, their own gates, read-mostly.',
}

/** Which of a band's sections this portal actually navigates. */
function reachedSections(portal: Portal, authority: Authority): string[] {
  return authority.sections.filter((id) => isValidSection(portal, id))
}

/**
 * How many real people land in each portal, from the roster the roster panel reads.
 *
 * The map alone says what a portal *buys*; it could not say who holds it, and a
 * permission model nobody is standing in is an abstraction. The two admin portals are
 * told apart the way the backend tells them apart — **by tenant**: an admin-tier
 * account with no tenant is platform staff (`platform_admin`, every tenant, pinned to
 * none), and an admin-tier account pinned to one is that tenant's own admin. The other
 * three portals are the role itself. `normalizeRole` folds every wire spelling onto the
 * four portal roles first and falls to `client` for an unknown one, so a role this
 * build has never seen is counted at the *least* privileged column, never the most.
 *
 * `null` in, `null` out: a roster that has not answered is not a roster of nobody.
 */
function holdersByPortal(users: AdminUser[] | null): Record<Portal, number> | null {
  if (users === null) return null
  const counts: Record<Portal, number> = {
    platform_admin: 0,
    tenant_admin: 0,
    ai_team: 0,
    devops: 0,
    client: 0,
  }
  for (const user of users) {
    const role = normalizeRole(user.role)
    if (role === 'admin') {
      counts[user.tenant_id == null ? 'platform_admin' : 'tenant_admin'] += 1
    } else {
      counts[role] += 1
    }
  }
  return counts
}

/**
 * Where each portal's authority stops — the permission model as one object.
 *
 * This screen's job is to make a reader believe there is a real access model behind
 * the dropdown, and three tables of names could not do it: they say who holds a word,
 * never what the word buys. A matrix does, and it does it without a paragraph — nine
 * bands of authority down the side, the five portals across the top, and a diagonal
 * that a person can read in one look as least privilege.
 *
 * **Every cell is derived, none is asserted.** `ROLE_SECTIONS` is the catalogue the
 * navigation itself renders, and every id in it is asserted against a live backend
 * route by `test_route_coverage.py`. So a portal that gains a section gains a mark
 * here on the next render, and this map cannot drift from the product the way a
 * hand-written permission table would.
 *
 * A mark is a filled tile **and** a tick **and** a screen-reader sentence: the tile
 * alone would be colour carrying the whole verdict, which DESIGN.md §2 forbids.
 */
export function DelegationMap({
  users = null,
  total = null,
}: {
  /** The roster in scope, so each column can say how many people stand in it. */
  users?: AdminUser[] | null
  /** Head-count in scope, or `null` while the roster has not answered. */
  total?: number | null
}): ReactElement {
  const holders = holdersByPortal(users)

  return (
    <DataPanel
      className="rounded-lg"
      eyebrow="aegis.rbac · lib/portal · ROLE_SECTIONS"
      title="Where each portal's authority stops"
      collapsible
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral" className="gap-1.5">
            <ShieldCheck className="size-3" aria-hidden />
            <Figure>{PORTALS.length}</Figure> portals
          </Badge>
          {total != null && (
            <Badge tone="neutral" className="gap-1.5">
              <Users className="size-3" aria-hidden />
              <Figure>{total}</Figure> {total === 1 ? 'holder' : 'holders'}
            </Badge>
          )}
          <InfoTip label="What this map is">
            Not a description of the permission model — the model itself. Each mark asks
            the console’s own routing catalogue whether that portal navigates a section
            carrying that authority. The backend asserts every section in that catalogue
            against a live route, so a portal that gains a surface gains a mark here, and
            a hand-written table of permissions can never drift away from it. The
            head-count under each portal is the live roster, split the way the backend
            splits it: an admin with no tenant is platform staff, an admin pinned to one
            is that tenant’s.
          </InfoTip>
        </div>
      }
      footer={
        <Receipt
          variant="inline"
          origin="lib/portal.ts · ROLE_SECTIONS · /admin/users"
          detail={
            holders == null
              ? 'each mark is computed from the catalogue the navigation renders; the roster has not answered, so no head-count is drawn'
              : 'each mark is computed, not recorded — the catalogue the navigation renders, counted against the live roster'
          }
        />
      }
    >
      <table className="w-full min-w-[46rem] border-collapse text-left">
        <caption className="sr-only">
          Bands of authority down the side, the five portals across the top. A tick means
          that portal navigates at least one section carrying that authority, and the
          head-count under each portal is how many people hold it.
        </caption>
        <thead>
          <tr>
            <th scope="col" className="eyebrow pb-3 pl-1 font-normal">
              Authority
            </th>
            {PORTALS.map((portal) => (
              <th
                key={portal}
                scope="col"
                className="px-2 pb-3 text-center align-bottom font-normal"
              >
                <span className="flex flex-col items-center gap-1">
                  <span className="flex items-center gap-1">
                    <span className="text-[0.8rem] leading-4 font-semibold text-foreground">
                      {PORTAL_SHORT[portal]}
                    </span>
                    <InfoTip label={`What the ${PORTAL_SHORT[portal]} portal is for`}>
                      {PORTAL_PURPOSE[portal]}
                    </InfoTip>
                  </span>
                  {/* Who is standing in it, and how much of the console it opens. A
                      portal nobody holds still exists, and says so with a zero. */}
                  <span
                    className={cn(
                      'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[0.6875rem]',
                      holders != null && holders[portal] > 0
                        ? 'bg-blue-100/70 text-blue-800'
                        : 'bg-surface-2 text-muted-foreground',
                    )}
                  >
                    <Users className="size-3 shrink-0" aria-hidden />
                    {holders == null ? (
                      <span>roster not loaded</span>
                    ) : (
                      <>
                        <Figure>{holders[portal]}</Figure>
                        <span>{holders[portal] === 1 ? 'holder' : 'holders'}</span>
                      </>
                    )}
                  </span>
                  <Figure className="text-[0.6875rem] text-muted-foreground">
                    {`${ROLE_SECTIONS[portal].length} sections`}
                  </Figure>
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {AUTHORITY.map((authority) => (
            <tr
              key={authority.id}
              className="border-t border-border transition-colors duration-[--dur-fast] motion-reduce:transition-none hover:bg-surface-2/60"
            >
              <th
                scope="row"
                className="min-w-0 py-2 pr-4 pl-1 text-left align-middle font-normal"
              >
                <span className="flex items-center gap-1.5">
                  <span className="text-[0.8125rem] font-medium text-foreground">
                    {authority.label}
                  </span>
                  <InfoTip label={authority.label}>
                    {authority.detail}{' '}
                    {authority.sections.length === 1
                      ? `Carried by the ${SECTIONS[authority.sections[0]].label} section.`
                      : `Carried by ${authority.sections.length} sections: ${authority.sections
                          .map((id) => SECTIONS[id].label)
                          .join(', ')}.`}
                  </InfoTip>
                </span>
              </th>
              {PORTALS.map((portal) => {
                const reached = reachedSections(portal, authority)
                const has = reached.length > 0
                const partial = has && reached.length < authority.sections.length
                return (
                  <td key={portal} className="px-2 py-2 text-center align-middle">
                    <span
                      title={
                        has
                          ? `${PORTAL_SHORT[portal]}: ${reached
                              .map((id) => SECTIONS[id].label)
                              .join(', ')}`
                          : `${PORTAL_SHORT[portal]}: none of ${authority.sections
                              .map((id) => SECTIONS[id].label)
                              .join(', ')}`
                      }
                      className={cn(
                        'inline-flex h-6 min-w-[2.75rem] items-center justify-center gap-1 rounded-md border text-[0.6875rem] font-medium transition-colors duration-[--dur-fast] motion-reduce:transition-none',
                        has
                          ? 'border-blue-600 bg-blue-600 text-white'
                          : 'border-border bg-surface-2 text-muted-foreground',
                      )}
                    >
                      {has ? (
                        <Check className="size-3.5 shrink-0" aria-hidden />
                      ) : (
                        <Minus className="size-3.5 shrink-0" aria-hidden />
                      )}
                      {partial ? (
                        <Figure className="text-[0.6875rem]">
                          {`${reached.length}/${authority.sections.length}`}
                        </Figure>
                      ) : null}
                      <span className="sr-only">
                        {has
                          ? partial
                            ? `${PORTAL_SHORT[portal]} reaches ${reached.length} of ${authority.sections.length} sections carrying ${authority.label}`
                            : `${PORTAL_SHORT[portal]} may ${authority.label.toLowerCase()}`
                          : `${PORTAL_SHORT[portal]} may not ${authority.label.toLowerCase()}`}
                      </span>
                    </span>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </DataPanel>
  )
}

/**
 * The catalogue check, run at module load in development.
 *
 * A band that names a section id which no longer exists would draw a column of blanks
 * that looks exactly like a portal correctly having no authority — the most expensive
 * kind of wrong on a screen whose whole job is being believed. This is cheap, runs
 * once, and says which id broke.
 */
if (process.env.NODE_ENV !== 'production') {
  for (const authority of AUTHORITY) {
    for (const id of authority.sections) {
      if (SECTIONS[id] === undefined) {
        // eslint-disable-next-line no-console
        console.error(
          `DelegationMap: authority "${authority.id}" names section "${id}", which is not in SECTIONS.`,
        )
      }
    }
  }
}
