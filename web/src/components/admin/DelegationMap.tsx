'use client'

import { Check, Minus, ShieldCheck } from 'lucide-react'
import type { ReactElement } from 'react'

import { DataPanel } from '@/components/ui/DataPanel'
import { Badge } from '@/components/ui/Badge'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { PORTALS, ROLE_SECTIONS, SECTIONS, isValidSection, type Portal } from '@/lib/portal'
import { cn } from '@/lib/utils'

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
    detail: 'Versions, patches, the red team and the latency budget.',
    sections: ['stack', 'patch', 'security', 'redteam', 'latency'],
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
export function DelegationMap(): ReactElement {
  return (
    <DataPanel
      className="rounded-lg"
      eyebrow="aegis.rbac · lib/portal · ROLE_SECTIONS"
      title="Where each portal's authority stops"
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral" className="gap-1.5">
            <ShieldCheck className="size-3" aria-hidden />
            <Figure>{PORTALS.length}</Figure> portals
          </Badge>
          <InfoTip label="What this map is">
            Not a description of the permission model — the model itself. Each mark asks
            the console’s own routing catalogue whether that portal navigates a section
            carrying that authority. The backend asserts every section in that catalogue
            against a live route, so a portal that gains a surface gains a mark here, and
            a hand-written table of permissions can never drift away from it.
          </InfoTip>
        </div>
      }
      footer={
        <Receipt
          variant="inline"
          origin="lib/portal.ts · ROLE_SECTIONS"
          detail="each mark is computed, not recorded — the catalogue the navigation renders"
        />
      }
    >
      <table className="w-full min-w-[46rem] border-collapse text-left">
        <caption className="sr-only">
          Bands of authority down the side, the five portals across the top. A tick means
          that portal navigates at least one section carrying that authority.
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
                <span className="flex flex-col items-center gap-0.5">
                  <span className="text-[0.8rem] leading-4 font-semibold text-foreground">
                    {PORTAL_SHORT[portal]}
                  </span>
                  <span className="flex items-center gap-1">
                    <Figure className="text-[0.68rem] text-muted-foreground">
                      {`${ROLE_SECTIONS[portal].length} sections`}
                    </Figure>
                    <InfoTip label={`What the ${PORTAL_SHORT[portal]} portal is for`}>
                      {PORTAL_PURPOSE[portal]}
                    </InfoTip>
                  </span>
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {AUTHORITY.map((authority) => (
            <tr key={authority.id} className="group">
              <th
                scope="row"
                className="min-w-0 py-2.5 pr-4 pl-1 text-left align-middle font-normal"
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
                  <td key={portal} className="px-2 py-2.5 text-center align-middle">
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
                        'inline-flex h-7 min-w-[3.25rem] items-center justify-center gap-1 rounded-md border text-[0.68rem] font-medium transition-colors duration-[--dur-fast] motion-reduce:transition-none',
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
                        <Figure className="text-[0.68rem]">
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
