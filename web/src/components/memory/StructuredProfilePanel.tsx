'use client'

import { IdCard } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import type { MemoryProfileResponse } from '@/lib/api/memory'

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
          <Badge key={String(v)} tone="neutral" className="text-[0.6rem]">
            {String(v).replace(/_/g, ' ')}
          </Badge>
        ))}
      </span>
    )
  }
  if (typeof value === 'number') {
    return <span className="tabular font-mono text-sm text-foreground">{value.toLocaleString('en-US')}</span>
  }
  return <span className="text-sm text-foreground">{String(value)}</span>
}

interface Props {
  state: AsyncState<MemoryProfileResponse>
}

/**
 * "Profile" (§4.3) — the consolidated key/value record of what the agent knows
 * about the subject, distilled from the facts and sessions. Just a clean list;
 * the "consolidated / structured" jargon moves to the ⓘ.
 */
export function StructuredProfilePanel({ state }: Props): ReactElement {
  const entries = state.status === 'ready' ? Object.entries(state.data.data) : ([] as [string, unknown][])

  return (
    <div className="flex h-full flex-col gap-3">
      <PanelHeader
        icon={IdCard}
        title="Profile"
        tint="bg-ml/12"
        ink="text-ml-ink"
        info="A stable, consolidated record of what the agent knows about this subject, distilled from its facts and past sessions."
        right={
          state.status === 'ready' ? (
            <span className="eyebrow text-[0.56rem]">updated {formatAgo(state.data.updated_at)}</span>
          ) : undefined
        }
      />

      {state.status === 'loading' && <LoadingRow label="Loading profile…" />}
      {state.status === 'error' && <ErrorRow message={state.message} />}
      {state.status === 'ready' && entries.length === 0 && (
        <EmptyRow>No profile has been consolidated for this subject yet.</EmptyRow>
      )}
      {state.status === 'ready' && entries.length > 0 && (
        <dl className="flex-1 divide-y divide-border/60">
          {entries.map(([k, v]) => (
            <div key={k} className="flex items-center justify-between gap-4 py-2.5">
              <dt className="eyebrow shrink-0 text-[0.6rem]">{humanizeKey(k)}</dt>
              <dd className="min-w-0 text-right">{renderValue(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
