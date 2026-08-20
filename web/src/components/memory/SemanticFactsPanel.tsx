'use client'

import { ChevronDown, Network } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { RevealOnScroll } from '@/components/shared'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/utils'
import type { MemoryFactRow, MemoryFactsResponse } from '@/lib/api/memory'

import { EmptyRow, ErrorRow, LoadingRow } from './StateRow'
import { PanelHeader } from './PanelHeader'
import { formatDate } from './datetime'
import type { AsyncState } from './useAsync'
import { factStatus, validFactCount } from './memoryText'

/**
 * The belief-history detail for one fact — the honest bitemporal machinery
 * (subject·predicate·object triple + valid → superseded window), kept one layer
 * down behind the row's toggle so the primary surface stays plain.
 */
function FactDetail({ fact }: { fact: MemoryFactRow }): ReactElement {
  const invalid = !fact.is_valid
  return (
    <div className="animate-reveal border-t border-border/60 px-3 py-2.5">
      <p className="font-mono text-[0.64rem] leading-relaxed text-muted-foreground">
        <span className="text-blue-600">{fact.subject}</span>
        {' · '}
        <span className="text-blue-700">{fact.predicate}</span>
        {' · '}
        <span className="text-blue-800">{fact.object}</span>
      </p>
      <p className="tabular mt-1.5 font-mono text-[0.6rem] text-muted-foreground">
        <span className="eyebrow mr-1.5 text-[0.54rem]">Valid</span>
        {formatDate(fact.valid_at)}
        {' → '}
        <span className={invalid ? 'text-block-ink' : 'text-ok-ink'}>
          {invalid ? formatDate(fact.invalid_at) : 'current'}
        </span>
        {fact.supersedes_id != null && <> · replaced fact #{fact.supersedes_id}</>}
      </p>
    </div>
  )
}

/** One compact fact row: statement · confidence · recalled count, detail on click. */
function FactRow({ fact }: { fact: MemoryFactRow }): ReactElement {
  const [open, setOpen] = useState(false)
  const status = factStatus(fact)
  const invalid = !fact.is_valid
  return (
    <li
      className={cn(
        'overflow-hidden rounded-lg border',
        invalid ? 'border-border/60 bg-surface-2/30' : 'border-border bg-card',
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-surface-2/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span
          className={cn(
            'min-w-0 flex-1 text-sm leading-snug',
            invalid ? 'text-muted-foreground line-through decoration-block/40' : 'font-medium text-foreground',
          )}
        >
          {fact.text}
        </span>
        <span
          className="tabular shrink-0 font-mono text-[0.68rem] text-foreground"
          title={`Confidence ${Math.round(fact.confidence * 100)}%`}
        >
          {Math.round(fact.confidence * 100)}%
        </span>
        <span className="tabular hidden shrink-0 font-mono text-[0.6rem] text-muted-foreground sm:inline">
          {fact.access_count}× recalled
        </span>
        <Badge tone={status.tone === 'ok' ? 'ok' : 'neutral'} className="shrink-0 text-[0.56rem]">
          {status.label}
        </Badge>
        <ChevronDown
          className={cn('size-3.5 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')}
        />
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
 * subject, as single-line rows (statement + confidence % + recall count). The
 * subject·predicate·object triple and the bitemporal validity window open on
 * click; a header toggle reveals superseded beliefs.
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
        tint="bg-blue-400/12"
        ink="text-blue-600"
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
                className="size-3 accent-[var(--blue-600)]"
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
        <ul className="flex flex-1 flex-col gap-1.5">
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
