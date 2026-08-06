import { GitBranch } from 'lucide-react'
import type { ReactElement } from 'react'

import { ErrorRow, LoadingRow } from '@/components/common/StateRow'
import { Badge } from '@/components/ui/badge'
import { formatAgo } from '@/lib/datetime'
import { cn } from '@/lib/utils'
import type { AsyncState } from '@/components/admin/useAsync'
import type { OpsPromptVersionRow, OpsPromptsResponse } from '@/types/ops'

import { PanelHead } from './PanelHead'

type StatusVariant = 'ok' | 'risk' | 'graph' | 'secondary'

const STATUS_STYLE: Record<string, { variant: StatusVariant; dot: string }> = {
  active: { variant: 'ok', dot: 'bg-ok' },
  staged: { variant: 'risk', dot: 'bg-risk' },
  draft: { variant: 'graph', dot: 'bg-graph' },
  archived: { variant: 'secondary', dot: 'bg-border' },
}

interface Props {
  state: AsyncState<OpsPromptsResponse>
  base: number | null
  target: number | null
  onSelect: (version: number) => void
}

/** One version row on the timeline (click to add/remove from the diff pair). */
function VersionRow({
  row,
  selected,
  role,
  onSelect,
}: {
  row: OpsPromptVersionRow
  selected: boolean
  role: 'base' | 'target' | null
  onSelect: (v: number) => void
}): ReactElement {
  const style = STATUS_STYLE[row.status] ?? STATUS_STYLE.archived
  return (
    <li className="relative flex gap-3">
      <span className={cn('z-10 mt-1 grid size-4 shrink-0 place-items-center rounded-full border-2 border-card', style.dot)} />
      <button
        type="button"
        onClick={() => onSelect(row.version)}
        className={cn(
          'min-w-0 flex-1 rounded-lg border p-3 text-left transition-colors',
          selected ? 'border-primary/40 bg-surface-2/60 ring-1 ring-primary/20' : 'border-border bg-card hover:bg-surface-2/40',
        )}
      >
        <div className="flex items-center gap-2">
          <span className="tabular font-display text-sm font-semibold text-foreground">v{row.version}</span>
          <Badge variant={style.variant} className="text-[0.58rem]">
            {row.status}
          </Badge>
          {role && (
            <span className="eyebrow rounded-sm bg-primary px-1.5 py-0.5 text-[0.54rem] text-primary-foreground">
              diff {role}
            </span>
          )}
          <span className="eyebrow ml-auto text-[0.56rem]">{formatAgo(row.created_at)}</span>
        </div>
        {row.notes && <p className="mt-1 text-xs leading-snug text-muted-foreground">{row.notes}</p>}
        <p className="mt-1 font-mono text-[0.6rem] text-muted-foreground/80">by {row.created_by ?? 'system'}</p>
      </button>
    </li>
  )
}

/**
 * The **Versions** timeline (§4.4) — every version of a prompt with its
 * lifecycle status (draft / staged / active / archived). Click two versions to
 * load the diff between them. Card-less: a `BentoTile` owns the surrounding Card.
 */
export function PromptTimeline({ state, base, target, onSelect }: Props): ReactElement {
  const rows = state.status === 'ready' ? state.data.rows : []
  return (
    <>
      <PanelHead
        icon={GitBranch}
        tint="bg-graph/12"
        ink="text-graph-ink"
        title="Versions"
        subtitle="Draft · staged · active · archived"
        right={<span className="eyebrow text-[0.56rem]">tap two to diff</span>}
      />
      {state.status === 'loading' && <LoadingRow label="Loading versions…" />}
      {state.status === 'error' && <ErrorRow message={state.message} />}
      {state.status === 'ready' && rows.length === 0 && (
        <div className="py-8 text-sm text-muted-foreground">No prompt versions recorded yet.</div>
      )}
      {state.status === 'ready' && rows.length > 0 && (
        <ol className="relative space-y-2.5 before:absolute before:top-2 before:bottom-2 before:left-[7px] before:w-0.5 before:bg-border/70">
          {rows.map((row) => (
            <VersionRow
              key={row.id}
              row={row}
              selected={row.version === base || row.version === target}
              role={row.version === base ? 'base' : row.version === target ? 'target' : null}
              onSelect={onSelect}
            />
          ))}
        </ol>
      )}
    </>
  )
}
