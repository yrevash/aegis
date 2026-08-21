'use client'

import { Building2, Globe2, Lock, type LucideIcon } from 'lucide-react'
import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { Badge } from '@/components/ui/Badge'
import type { MemorySubjectRow } from '@/lib/api/memory'

/**
 * Who can reach this record — the memory subsystem's central claim, drawn.
 *
 * **The claim, stated exactly.** A memory is private to one person by construction:
 * `recall()` filters `subject_id` **and** `tenant_id` on every arm, and the adapter
 * seam composes exactly one subject shape per user. There is no platform- or
 * tenant-wide memory *bucket* — nothing here suggests otherwise. What widens with rank
 * is the **reach over those records**: a plain principal manages one subject, a tenant
 * administrator manages every subject inside its own tenant, and a platform admin
 * spans tenants.
 *
 * So this is three rungs, not a pyramid of shared data:
 *
 *   1. **the person** — the record itself, and the only rung that holds facts
 *   2. **the tenant's administrator** — may read and correct it, and nothing outside
 *      the tenant
 *   3. **the platform operator** — spans tenants, and is refused nothing by tenancy
 *
 * **Every figure is a field, or it is an {@link Absence}.** The counts come off
 * `GET /memory/subjects` rows. When the signed-in principal is a plain user its own
 * list holds exactly one row — which is the isolation working — and this component
 * therefore *cannot* count the other people in the tenant. It says so, in the slot the
 * number would occupy, rather than showing a zero that would read as "nobody else".
 */

interface ScopeLadderProps {
  /** Every subject this sign-in can see — one row for a plain principal. */
  rows: readonly MemorySubjectRow[]
  /** The subject currently open. */
  selected: string | null
  /** Whether the server said this principal manages more than itself. */
  canManageOthers: boolean
}

/** One rung. `reach` is what this tier may touch; `holds` is what it stores itself. */
function Rung({
  icon: Icon,
  tier,
  reach,
  detail,
  tone,
  children,
}: {
  icon: LucideIcon
  tier: string
  reach: string
  detail?: string
  tone: 'ok' | 'agent' | 'neutral'
  children?: ReactElement | null
}): ReactElement {
  return (
    <li className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
      <span
        aria-hidden
        className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-surface-2"
      >
        <Icon className="size-4 text-blue-700" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">{tier}</span>
          <Badge tone={tone}>{reach}</Badge>
        </p>
        {detail ? <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p> : null}
        {children}
      </div>
    </li>
  )
}

/**
 * A single subject's holding, as a bar against the largest holding on screen.
 *
 * One hue at one intensity — this is a magnitude, not a status, and nothing about a
 * larger memory is better or worse than a smaller one.
 */
function Holding({
  row,
  max,
  current,
}: {
  row: MemorySubjectRow
  max: number
  current: boolean
}): ReactElement {
  const pct = max > 0 ? Math.max(row.fact_count > 0 ? 4 : 0, (row.fact_count / max) * 100) : 0
  return (
    <li className="flex items-center gap-2.5 py-1">
      <span
        className={`w-28 shrink-0 truncate text-xs ${
          current ? 'font-medium text-foreground' : 'text-muted-foreground'
        }`}
      >
        {row.label}
      </span>
      <span
        aria-hidden
        className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-sm bg-surface-2"
      >
        <span
          className={`block h-full rounded-sm ${current ? 'bg-blue-600' : 'bg-blue-400'}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <Figure className="w-10 shrink-0 text-right text-xs text-muted-foreground">
        {row.fact_count}
      </Figure>
    </li>
  )
}

/** The reach ladder for the open subject. */
export function ScopeLadder({
  rows,
  selected,
  canManageOthers,
}: ScopeLadderProps): ReactElement {
  const current = rows.find((row) => row.subject === selected) ?? null
  const tenantId = current?.tenant_id ?? null
  const max = rows.reduce((n, row) => Math.max(n, row.fact_count), 0)
  const others = rows.filter((row) => !row.is_self)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="t-title text-foreground">Who can reach this record</h3>
        <InfoTip label="What is scoped, and what is not">
          Recall filters subject and tenant on every arm — what widens with rank is the reach
          over those records, never the records themselves.
        </InfoTip>
      </div>

      <ul className="divide-y divide-border/60">
        <Rung
          icon={Lock}
          tier={current ? current.label : 'This person'}
          reach="the record itself"
          tone="ok"
          detail={
            current
              ? `${current.fact_count} durable fact${current.fact_count === 1 ? '' : 's'}${
                  current.session_count > 0 ? `, ${current.session_count} session` : ''
                }${current.session_count > 1 ? 's' : ''} — nobody else's are in here`
              : undefined
          }
        />
        <Rung
          icon={Building2}
          tier={tenantId == null ? 'No tenant administrator' : `Tenant ${tenantId}'s administrator`}
          reach={tenantId == null ? 'nothing to administer' : 'read and correct, inside the tenant'}
          tone={tenantId == null ? 'neutral' : 'agent'}
          detail={
            tenantId == null
              ? 'This record sits in the platform slice, which no tenant administrator holds.'
              : undefined
          }
        >
          {tenantId == null ? null : canManageOthers ? (
            <ul className="mt-2">
              {rows
                .filter((row) => row.tenant_id === tenantId)
                .map((row) => (
                  <Holding
                    key={row.subject}
                    row={row}
                    max={max}
                    current={row.subject === selected}
                  />
                ))}
            </ul>
          ) : (
            <div className="mt-2">
              <Absence
                figure="The other people in this tenant"
                why="This sign-in manages one subject, so it was served exactly one row — the isolation working, not a gap."
              />
            </div>
          )}
        </Rung>
        <Rung
          icon={Globe2}
          tier="The platform operator"
          reach="spans every tenant"
          tone="neutral"
          detail={
            canManageOthers && others.length > 0
              ? `${rows.length} subject${rows.length === 1 ? '' : 's'} visible to this sign-in`
              : 'Aegis itself, which is refused nothing by tenancy — every read of it lands on the audit trail.'
          }
        />
      </ul>

      {/* The one receipt treatment, not a sixth hand-rolled `Source:` line. */}
      <Receipt origin="GET /memory/subjects" detail="built from the server's own tenant scope" />
    </div>
  )
}
