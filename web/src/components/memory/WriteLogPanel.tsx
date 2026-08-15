'use client'

import { ChevronDown, FilePlus2, FileX2, History, PencilLine } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { RevealOnScroll } from '@/components/shared'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'
import type { MemoryWriteRow, MemoryWritesResponse } from '@/lib/api/memory'

import { EmptyRow, ErrorRow, LoadingRow } from './StateRow'
import { PanelHeader } from './PanelHeader'
import { formatAgo } from './datetime'
import type { AsyncState } from './useAsync'

/** Op → plain verb + icon + badge tone (drops the ADD/UPDATE/INVALIDATE codes). */
const OP_STYLE: Record<string, { icon: LucideIcon; tone: BadgeTone; label: string }> = {
  ADD: { icon: FilePlus2, tone: 'ok', label: 'Learned' },
  UPDATE: { icon: PencilLine, tone: 'graph', label: 'Updated' },
  INVALIDATE: { icon: FileX2, tone: 'block', label: 'Retired' },
  NOOP: { icon: PencilLine, tone: 'neutral', label: 'Reviewed' },
}

/** A compact key=value diff of a before/after snapshot. */
function Delta({ row }: { row: MemoryWriteRow }): ReactElement | null {
  const keys = Array.from(new Set([...Object.keys(row.before), ...Object.keys(row.after)]))
  if (keys.length === 0) return null
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[0.62rem]">
      {keys.map((k) => {
        const before = row.before[k]
        const after = row.after[k]
        const changed = JSON.stringify(before) !== JSON.stringify(after)
        return (
          <span key={k} className="text-muted-foreground">
            {k}:{' '}
            {before !== undefined && (
              <span className={changed ? 'text-block-ink line-through' : ''}>{String(before)}</span>
            )}
            {changed && after !== undefined && (
              <>
                {before !== undefined && ' → '}
                <span className="text-ok-ink">{String(after)}</span>
              </>
            )}
            {before === undefined && after !== undefined && !changed && (
              <span className="text-ok-ink">{String(after)}</span>
            )}
          </span>
        )
      })}
    </div>
  )
}

/**
 * One changelog entry. The default view is the plain story — what changed, to
 * which fact, when, and why. The raw before/after triple, the writing model and
 * the trace id are debug identifiers, so they sit behind the row's toggle.
 */
function WriteRow({ row }: { row: MemoryWriteRow }): ReactElement {
  const [open, setOpen] = useState(false)
  const style = OP_STYLE[row.op] ?? OP_STYLE.NOOP
  const Icon = style.icon
  // Bitemporal supersession is the story, not debug detail — keep it on top.
  const supersedes = row.after.supersedes_id

  return (
    <li className="relative flex gap-3 pl-0">
      <span className="z-10 mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border border-border bg-card">
        <Icon className="size-3 text-muted-foreground" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Badge tone={style.tone}>{style.label}</Badge>
          {row.fact_id != null && (
            <span className="font-mono text-[0.62rem] text-muted-foreground">fact #{row.fact_id}</span>
          )}
          {supersedes != null && (
            <span className="font-mono text-[0.62rem] text-block-ink">supersedes #{String(supersedes)}</span>
          )}
          <span className="eyebrow ml-auto text-[0.56rem]">{formatAgo(row.ts)}</span>
        </div>
        <div className="mt-1 flex items-start gap-2">
          {row.reason && <p className="min-w-0 flex-1 text-xs leading-snug text-foreground">{row.reason}</p>}
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-label={`Detail for write #${row.id}`}
            className="shrink-0 rounded text-muted-foreground transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ChevronDown className={cn('size-3.5 transition-transform', open && 'rotate-180')} />
          </button>
        </div>
        {open && (
          <div className="animate-reveal mt-1.5 rounded-md border border-border/60 bg-surface-2/40 p-2">
            <Delta row={row} />
            <p className="mt-1 font-mono text-[0.6rem] text-muted-foreground/80">
              {row.model ?? 'system'}
              {row.trace_id ? ` · ${row.trace_id}` : ''}
            </p>
          </div>
        )}
      </div>
    </li>
  )
}

interface Props {
  state: AsyncState<MemoryWritesResponse>
}

/**
 * "Recent updates" (§4.3) — the timeline of every memory change the agent made
 * (learned / updated / retired) with its reason and any supersession. The raw
 * before/after triple, the writing model and the trace id open per row. The
 * append-only guarantee is kept as a small badge with the honest detail in its
 * ⓘ. (Underneath: the append-only memory write-log.)
 */
export function WriteLogPanel({ state }: Props): ReactElement {
  const rows = state.status === 'ready' ? state.data.rows : []
  return (
    <div className="flex h-full flex-col gap-3">
      <PanelHeader
        icon={History}
        title="Recent updates"
        tint="bg-surface-2"
        ink="text-muted-foreground"
        info="Every change to the agent's memory — what it learned, updated, or retired, and why."
        right={
          <span className="inline-flex items-center gap-1">
            <Badge tone="neutral" className="text-[0.56rem]">
              append-only
            </Badge>
            <InfoTip label="About append-only">
              Updates are never overwritten — each change is recorded as a new entry, so the full
              history stays auditable.
            </InfoTip>
          </span>
        }
      />

      {state.status === 'loading' && <LoadingRow label="Loading updates…" />}
      {state.status === 'error' && <ErrorRow message={state.message} />}
      {state.status === 'ready' && rows.length === 0 && (
        <EmptyRow>No updates yet. Changes appear here, newest first.</EmptyRow>
      )}
      {state.status === 'ready' && rows.length > 0 && (
        <ol className="relative flex-1 space-y-3 before:absolute before:top-2 before:bottom-2 before:left-[9px] before:w-px before:bg-border/70">
          {rows.map((row, i) => (
            <RevealOnScroll key={row.id} delayMs={i * 40}>
              <WriteRow row={row} />
            </RevealOnScroll>
          ))}
        </ol>
      )}
    </div>
  )
}
