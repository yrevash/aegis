'use client'

import { ChevronDown, Network } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { RevealOnScroll } from '@/components/shared'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/utils'
import type { MemoryFactRow, MemoryFactsResponse } from '@/lib/api/memory'

import { EmptyRow, ErrorRow, LoadingRow } from './StateRow'
import { MiniMeter } from './MiniMeter'
import { PanelHeader } from './PanelHeader'
import { formatDate } from './datetime'
import type { AsyncState } from './useAsync'
import { factStatus, validFactCount } from './memoryText'

/**
 * The belief-history detail for one fact — the honest bitemporal machinery
 * (subject·predicate·object triple + valid → superseded window), kept one layer
 * down behind the row's "detail" toggle so the primary surface stays plain.
 */
function FactDetail({ fact }: { fact: MemoryFactRow }): ReactElement {
  const invalid = !fact.is_valid
  return (
    <div className="animate-reveal mt-2.5 rounded-md border border-border/70 bg-surface-2/40 p-2.5">
      <p className="font-mono text-[0.64rem] leading-relaxed text-muted-foreground">
        <span className="text-graph-ink">{fact.subject}</span>
        {' · '}
        <span className="text-agent-ink">{fact.predicate}</span>
        {' · '}
        <span className="text-ml-ink">{fact.object}</span>
      </p>
      <div className="mt-2 flex items-center gap-2">
        <span className="eyebrow text-[0.54rem]">Belief history</span>
        <span className="tabular font-mono text-[0.6rem] text-muted-foreground">{formatDate(fact.valid_at)}</span>
        <div className="relative h-1 flex-1 overflow-hidden rounded-full bg-surface-2">
          <div
            className={cn('absolute inset-y-0 left-0 rounded-full', invalid ? 'bg-block/60' : 'bg-graph/70')}
            style={{ width: invalid ? '100%' : '78%' }}
          />
        </div>
        <span className={cn('tabular font-mono text-[0.6rem]', invalid ? 'text-block-ink' : 'text-ok-ink')}>
          {invalid ? formatDate(fact.invalid_at) : 'current'}
        </span>
      </div>
      {fact.supersedes_id != null && (
        <p className="mt-1.5 font-mono text-[0.58rem] text-muted-foreground">replaced fact #{fact.supersedes_id}</p>
      )}
    </div>
  )
}

/** One compact fact row: statement · confidence meter · recalled count · detail. */
function FactRow({ fact }: { fact: MemoryFactRow }): ReactElement {
  const [open, setOpen] = useState(false)
  const status = factStatus(fact)
  const invalid = !fact.is_valid
  return (
    <li className={cn('rounded-lg border p-3', invalid ? 'border-border/60 bg-surface-2/30' : 'border-border bg-card')}>
      <div className="flex items-start gap-2">
        <p
          className={cn(
            'flex-1 text-sm leading-snug',
            invalid ? 'text-muted-foreground line-through decoration-block/40' : 'font-medium text-foreground',
          )}
        >
          {fact.text}
        </p>
        <Badge tone={status.tone === 'ok' ? 'ok' : 'neutral'} className="shrink-0 text-[0.56rem]">
          {status.label}
        </Badge>
      </div>

      <div className="mt-2.5 flex items-center gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span className="eyebrow shrink-0 text-[0.54rem]">Confidence</span>
          <MiniMeter value={fact.confidence} hex="var(--ml)" height={5} />
          <span className="tabular shrink-0 font-mono text-[0.62rem] text-foreground">
            {Math.round(fact.confidence * 100)}%
          </span>
        </div>
        <span className="tabular shrink-0 font-mono text-[0.6rem] text-muted-foreground">
          recalled {fact.access_count}×
        </span>
      </div>

      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="mt-2 inline-flex items-center gap-1 rounded text-[0.62rem] text-muted-foreground transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronDown className={cn('size-3 transition-transform', open && 'rotate-180')} />
        detail
      </button>
      {open && <FactDetail fact={fact} />}
    </li>
  )
}

interface Props {
  state: AsyncState<MemoryFactsResponse>
}

/**
 * "What we know" (§4.3) — the semantic facts the agent believes about the
 * subject, as compact rows (statement + confidence meter + recall count). The
 * subject·predicate·object triple and the bitemporal validity window move behind
 * a per-row detail toggle; a header toggle reveals superseded beliefs.
 */
export function SemanticFactsPanel({ state }: Props): ReactElement {
  const [showSuperseded, setShowSuperseded] = useState(true)
  const all = state.status === 'ready' ? state.data.rows : []
  const rows = showSuperseded ? all : all.filter((r) => r.is_valid)
  const validCount = validFactCount(all)

  return (
    <div className="flex h-full flex-col gap-3">
      <PanelHeader
        icon={Network}
        title="What we know"
        tint="bg-graph/12"
        ink="text-graph-ink"
        info="The facts the agent believes about this subject, each scored by confidence. Superseded facts are kept as belief history."
        right={
          <>
            {state.status === 'ready' && (
              <Badge tone="graph" className="text-[0.58rem]">
                {validCount} current
              </Badge>
            )}
            <label className="flex cursor-pointer items-center gap-1.5 text-[0.62rem] text-muted-foreground">
              <input
                type="checkbox"
                checked={showSuperseded}
                onChange={(e) => setShowSuperseded(e.target.checked)}
                className="size-3 accent-[var(--graph-ink)]"
              />
              superseded
            </label>
          </>
        }
      />

      {state.status === 'loading' && <LoadingRow label="Loading facts…" />}
      {state.status === 'error' && <ErrorRow message={state.message} />}
      {state.status === 'ready' && rows.length === 0 && (
        <EmptyRow>No facts recorded for this subject yet.</EmptyRow>
      )}
      {state.status === 'ready' && rows.length > 0 && (
        <ul className="flex flex-1 flex-col gap-2">
          {rows.map((f, i) => (
            <RevealOnScroll key={f.id} delayMs={i * 40}>
              <FactRow fact={f} />
            </RevealOnScroll>
          ))}
        </ul>
      )}
    </div>
  )
}
