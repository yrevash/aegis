'use client'

import { IdCard } from 'lucide-react'
import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { Badge } from '@/components/ui/Badge'
import type { MemoryProfileResponse } from '@/lib/api/memory'

/** Built once — constructing an `Intl.NumberFormat` per cell is the costly half. */
const NUMBER_FORMAT = new Intl.NumberFormat(undefined)

import { EmptyRow, ErrorRow, LoadingRow } from './StateRow'
import { PanelHeader } from './PanelHeader'
import { formatAgo } from './datetime'
import type { AsyncState } from './useAsync'
import { humanizeKey } from './memoryText'

/** Render a scalar / array profile value into a compact display element. */
function renderValue(value: unknown): ReactElement {
  if (Array.isArray(value)) {
    return (
      <span className="flex flex-wrap justify-end gap-1">
        {value.map((v) => (
          <Badge key={String(v)} tone="neutral" className="px-2 text-[0.6875rem]">
            {String(v).replace(/_/g, ' ')}
          </Badge>
        ))}
      </span>
    )
  }
  if (typeof value === 'number') {
    // The reader's locale, not a pinned `en-US` — a hardcoded grouping separator
    // is a hardcoded number format however it is spelled.
    return (
      <Figure className="text-[0.8rem] leading-5 text-foreground">
        {NUMBER_FORMAT.format(value)}
      </Figure>
    )
  }
  return <span className="min-w-0 text-[0.8rem] break-words text-foreground">{String(value)}</span>
}

interface Props {
  state: AsyncState<MemoryProfileResponse>
}

/**
 * "Profile" (§4.3) — the consolidated key/value record of what the agent knows
 * about the subject, distilled from the facts and sessions. A two-column record
 * of label/value cells; the "consolidated / structured" jargon moves to the ⓘ.
 */
export function StructuredProfilePanel({ state }: Props): ReactElement {
  const entries = state.status === 'ready' ? Object.entries(state.data.data) : ([] as [string, unknown][])

  return (
    <div className="flex flex-col gap-3">
      <PanelHeader
        icon={IdCard}
        title="Profile"
        tint="bg-blue-100/12"
        ink="text-blue-800"
        info="A stable, consolidated record of what the agent knows about this subject, distilled from its facts and past sessions."
        right={
          state.status === 'ready' ? (
            <span className="eyebrow text-[0.6875rem]">updated {formatAgo(state.data.updated_at)}</span>
          ) : undefined
        }
      />

      {state.status === 'loading' && <LoadingRow label="Loading profile…" />}
      {state.status === 'error' && <ErrorRow message={state.message} />}
      {state.status === 'ready' && entries.length === 0 && (
        <EmptyRow>No profile has been consolidated for this subject yet.</EmptyRow>
      )}
      {state.status === 'ready' && entries.length > 0 && (
        // Two columns of stacked label/value cells: the same twelve-odd fields
        // in roughly half the vertical run. List-valued fields (risk flags,
        // entitlements) take the full width so their chips stay on one line.
        <dl className="grid grid-cols-2 gap-x-4 border-t border-border/60">
          {entries.map(([k, v]) =>
            Array.isArray(v) ? (
              // List fields run full width with their chips on the label's line.
              <div
                key={k}
                className="col-span-2 flex min-w-0 items-center justify-between gap-4 border-b border-border/60 py-1.5"
              >
                <dt className="eyebrow shrink-0 text-[0.6875rem]">{humanizeKey(k)}</dt>
                <dd className="min-w-0">{renderValue(v)}</dd>
              </div>
            ) : (
              <div key={k} className="min-w-0 border-b border-border/60 py-1.5">
                <dt className="eyebrow text-[0.6875rem]">{humanizeKey(k)}</dt>
                <dd className="mt-0.5 min-w-0">{renderValue(v)}</dd>
              </div>
            ),
          )}
        </dl>
      )}
    </div>
  )
}
