'use client'

import { ArrowRight, ChevronDown, MessagesSquare, Network, ScanSearch, Search, Terminal } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { getRecallDebug } from '@/lib/api/client'
import { RevealOnScroll } from '@/components/shared'
import { Badge } from '@/components/ui/Badge'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/primitives/button'
import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'
import type { RecallDebugItem } from '@/lib/api/memory'

import { ErrorRow, LoadingRow } from './StateRow'
import { MiniMeter } from './MiniMeter'
import { useAsync } from './useAsync'
import { RECALL_DIMENSIONS, TOKEN_BUDGET, budgetPct, recallScores } from './memoryText'

/** One ranked recall row with its three plain sub-scores as calm meters. */
function RecallRow({ item }: { item: RecallDebugItem }): ReactElement {
  const scores = recallScores(item)
  return (
    <li
      className={cn(
        'rounded-lg border p-3',
        item.injected ? 'border-border bg-card' : 'border-dashed border-border/70 bg-surface-2/30 opacity-80',
      )}
    >
      <div className="flex items-start gap-2">
        <p className="flex-1 text-xs leading-snug text-foreground">{item.text}</p>
        <Badge tone={item.injected ? 'ok' : 'neutral'} className="shrink-0 text-[0.54rem]">
          {item.injected ? 'used' : 'not used'}
        </Badge>
      </div>
      <div className="mt-2.5 grid grid-cols-3 gap-2.5">
        {RECALL_DIMENSIONS.map((dim) => (
          <div key={dim.key}>
            <div className="mb-1 flex items-center gap-1">
              <span className="eyebrow text-[0.5rem]">{dim.label}</span>
              <InfoTip label={dim.label} className="size-3">
                {dim.hint}
              </InfoTip>
            </div>
            <MiniMeter value={scores[dim.key]} hex={dim.hex} height={4} />
          </div>
        ))}
      </div>
    </li>
  )
}

/** A titled, dot-marked group of ranked recall rows (Facts or Sessions). */
function RecallGroup({
  title,
  dotClass,
  count,
  items,
  icon: Icon,
}: {
  title: string
  dotClass: string
  count: number
  items: RecallDebugItem[]
  icon: typeof Network
}): ReactElement {
  return (
    <div>
      <div className="flex items-center gap-1.5">
        <Icon className="size-3.5 text-muted-foreground" />
        <span className="t-label text-foreground">{title}</span>
        <span className={cn('size-2 rounded-full', dotClass)} />
        <span className="ml-auto font-mono text-[0.6rem] text-muted-foreground">{count} used</span>
      </div>
      <ul className="mt-1.5 space-y-2">
        {items.map((item, i) => (
          <RevealOnScroll key={item.key} delayMs={i * 40}>
            <RecallRow item={item} />
          </RevealOnScroll>
        ))}
      </ul>
    </div>
  )
}

interface Props {
  token: string | null
  subject: string
}

/**
 * "Why did it recall this?" (§4.3) — the flagship recall trace, kept as an
 * expander. Given a query it shows the ranked memories, each with
 * Match / Fresh / Weight meters (the honest relevance × recency × importance, in
 * plain words), which cleared the cut, the assembled context the model sees, and
 * how much of the context budget it used.
 */
export function RecallDebugPanel({ token, subject }: Props): ReactElement {
  const [open, setOpen] = useState(true)
  const [draft, setDraft] = useState('Refund status for the duplicate charge on A-771')
  const [query, setQuery] = useState(draft)
  const { state } = useAsync(() => getRecallDebug(token, subject, query), [token, subject, query])

  const data = state.status === 'ready' ? state.data : null
  const usedPct = data ? budgetPct(data.tokens_used) : 0

  return (
    <Card className="overflow-hidden">
      <div className="flex w-full items-center gap-2 px-5 py-3.5 transition-colors hover:bg-surface-2/40">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex flex-1 items-center gap-2 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="grid size-6 place-items-center rounded-md bg-agent/12">
            <ScanSearch className="size-3.5 text-agent-ink" />
          </span>
          <span className="t-title text-foreground">Why did it recall this?</span>
        </button>
        <InfoTip label="About recall">
          For a given question, this shows which memories the agent pulled in and how it ranked
          them — by how well they match, how recent they are, and how important they are.
        </InfoTip>
        <Badge tone="neutral" className="text-[0.56rem]">
          admin
        </Badge>
        <ChevronDown className={cn('size-4 text-muted-foreground transition-transform', open && 'rotate-180')} />
      </div>

      {open && (
        <div className="animate-reveal space-y-4 border-t border-border/70 px-5 py-4">
          <form
            className="flex flex-col gap-2 sm:flex-row"
            onSubmit={(e) => {
              e.preventDefault()
              setQuery(draft.trim() || 'refund status')
            }}
          >
            <div className="relative flex-1">
              <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Ask what the agent would recall…"
                className="h-9 w-full rounded-md border border-input bg-surface pr-3 pl-9 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
              />
            </div>
            <Button type="submit" className="shrink-0">
              Show recall <ArrowRight />
            </Button>
          </form>

          {state.status === 'loading' && <LoadingRow label="Gathering memories…" />}
          {state.status === 'error' && <ErrorRow message={state.message} />}

          {data && (
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="space-y-3">
                <RecallGroup
                  title="Facts"
                  icon={Network}
                  dotClass="bg-graph"
                  count={data.recalled_fact_count}
                  items={data.facts}
                />
                <RecallGroup
                  title="Sessions"
                  icon={MessagesSquare}
                  dotClass="bg-agent"
                  count={data.recalled_message_count}
                  items={data.episodic}
                />
              </div>

              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <Terminal className="size-3.5 text-ml-ink" />
                  <span className="t-label text-foreground">What the agent sees</span>
                  <InfoTip label="About context">
                    The block of memory text assembled and handed to the model for this question.
                  </InfoTip>
                </div>
                <pre className="flex-1 overflow-auto rounded-lg border border-border bg-surface-2/50 p-3 font-mono text-[0.68rem] leading-relaxed whitespace-pre-wrap text-foreground">
                  {data.working_memory}
                </pre>
                <div>
                  <div className="flex items-center justify-between">
                    <span className="eyebrow text-[0.58rem]">Context used</span>
                    <span className="tabular font-mono text-[0.62rem] text-foreground">
                      {data.tokens_used} / {TOKEN_BUDGET} tokens
                    </span>
                  </div>
                  <MiniMeter
                    value={usedPct / 100}
                    hex={usedPct > 90 ? 'var(--risk)' : 'var(--ml)'}
                    height={8}
                    className="mt-1"
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
