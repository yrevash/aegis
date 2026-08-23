'use client'

import { ChevronDown, Network } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { BarChart } from '@/components/charts/BarChart'
import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { TAP_TARGET } from '@/components/primitives/tapTarget'
import { RevealOnScroll } from '@/components/shared'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/utils'
import type { MemoryFactRow, MemoryFactsResponse } from '@/lib/api/memory'

import { EmptyRow, ErrorRow, LoadingRow } from './StateRow'
import { PanelHeader } from './PanelHeader'
import { formatDate } from './datetime'
import type { AsyncState } from './useAsync'
import { confidenceBands, medianImportance, totalRecalls } from './memoryCharts'
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
      <p translate="no" className="font-mono text-[0.64rem] leading-relaxed break-words text-muted-foreground">
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
        className="flex w-full flex-col items-start gap-1 px-3 py-2 text-left transition-colors hover:bg-surface-2/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {/*
          **The fact gets its own line. The metadata sits under it.**

          This row was `flex items-center` with the statement in `flex-1` beside
          *four* `shrink-0` siblings — confidence, recall count, a status badge and
          the chevron. In this panel's home, the console's memory rail, the column
          is 21rem; minus padding and three gaps the four fixed items claimed most
          of it and left the statement about 110px, so a sentence like "every case
          opened here is a merchandise-order case under 16 CFR 435" wrapped at one
          or two words per line and a single fact ran the height of the viewport.

          The trigger was `sm:inline` on the recall count. `sm` is a **viewport**
          breakpoint: at 1512px it fires, whatever the container is doing. That is
          the third time a viewport query has been used to size something inside a
          narrow column in this codebase, and stacking removes the class of bug
          rather than re-tuning one instance of it — the statement is the content,
          and content should not compete with its own annotations for width.
        */}
        <span
          className={cn(
            'w-full text-sm leading-snug',
            invalid ? 'text-muted-foreground line-through decoration-block/40' : 'font-medium text-foreground',
          )}
        >
          {fact.text}
        </span>
        <span className="flex w-full items-center gap-2">
          <Figure
            label={`Confidence ${Math.round(fact.confidence * 100)} percent`}
            className="shrink-0 text-[0.68rem] leading-4 text-muted-foreground"
          >
            {Math.round(fact.confidence * 100)}%
          </Figure>
          <Figure
            label={`Recalled ${fact.access_count} times`}
            className="shrink-0 text-[0.6rem] leading-4 text-muted-foreground"
          >
            {fact.access_count}× recalled
          </Figure>
          <Badge tone={status.tone === 'ok' ? 'ok' : 'neutral'} className="shrink-0 text-[0.56rem]">
            {status.label}
          </Badge>
          <ChevronDown
            aria-hidden
            className={cn(
              'ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none',
              open && 'rotate-180',
            )}
          />
        </span>
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
  // `confidence`, `importance` and `access_count` are on every row and were
  // rendered nowhere but a per-row percentage. The distribution is the panel's
  // headline; the other two close it as measured detail.
  const bands = confidenceBands(all)
  const populatedBands = bands.filter((b) => b.facts > 0).length
  const median = medianImportance(all)
  const recalls = totalRecalls(all)

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
            <label
              htmlFor="facts-show-superseded"
              className="flex cursor-pointer items-center gap-1.5 text-[0.62rem] text-muted-foreground"
            >
              <input
                id="facts-show-superseded"
                name="show-superseded"
                type="checkbox"
                checked={showSuperseded}
                onChange={(e) => setShowSuperseded(e.target.checked)}
                /* A 0.75rem box is 15px at the largest text setting; the drawn tick
                   stays that size and the pointer target around it clears 24px. */
                className={cn(
                  'size-3 accent-[var(--blue-600)] outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  TAP_TARGET,
                )}
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

      {bands.length > 0 && (
        <div className="min-w-0">
          <p className="eyebrow mb-1">confidence</p>
          {/*
            **The plot is sized to the distribution it holds.** Five fixed bands
            with one of them populated is the common shape on a young record, and
            a 148px plot around a single bar left most of this card as whitespace.
            The bands themselves stay — dropping the empty ones would rescale a
            fixed 0–100% axis and misstate where the mass sits — but the height
            drops to the compact step when there is only one bar to draw.
          */}
          <BarChart
            allowDecimals={false}
            data={bands}
            index="band"
            category="facts"
            color="graph"
            valueFormatter={(v) => `${v} ${v === 1 ? 'fact' : 'facts'}`}
            height={populatedBands <= 1 ? 104 : 148}
          />
        </div>
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

      {state.status === 'ready' && all.length > 0 && (
        <Receipt
          origin="GET /memory/facts"
          detail={`${validCount} current${median === null ? '' : ` · median importance ${median}`} · ${recalls} recalls`}
        />
      )}
    </div>
  )
}
