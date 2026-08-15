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

/**
 * One ranked recall row. The headline is used / not used plus the match score
 * that drove the rank; the three plain sub-scores (Match · Fresh · Weight) open
 * on click so the panel is not a wall of meters.
 */
function RecallRow({ item }: { item: RecallDebugItem }): ReactElement {
  const [open, setOpen] = useState(false)
  const scores = recallScores(item)
  return (
    <li
      className={cn(
        'overflow-hidden rounded-lg border',
        item.injected ? 'border-border bg-card' : 'border-dashed border-border/70 bg-surface-2/30 opacity-80',
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-surface-2/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="min-w-0 flex-1 text-xs leading-snug text-foreground">{item.text}</span>
        <span
          className="tabular shrink-0 font-mono text-[0.66rem] text-foreground"
          title={`Match ${item.score.toFixed(2)}`}
        >
          {item.score.toFixed(2)}
        </span>
        <Badge tone={item.injected ? 'ok' : 'neutral'} className="shrink-0 text-[0.54rem]">
          {item.injected ? 'used' : 'not used'}
        </Badge>
        <ChevronDown
          className={cn('size-3 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')}
        />
      </button>
      {open && (
        <div className="animate-reveal grid grid-cols-3 gap-2.5 border-t border-border/60 px-3 py-2.5">
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
      )}
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
      <ul className="mt-1.5 space-y-1.5">
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
 * expander. Given a query it shows which memories cleared the cut (used /
 * not used) with the match score that ranked them; the Match / Fresh / Weight
 * breakdown (the honest relevance × recency × importance, in plain words) opens
 * per row. Beside it sits the assembled context the model sees, captioned with
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

              {/* The glass-box payoff, framed as a deliberate artefact: a
                  captioned block with the context budget on its own header rule. */}
              <figure className="flex flex-col overflow-hidden rounded-lg border border-border bg-surface-2/40">
                <figcaption className="flex items-center gap-2 border-b border-border/70 bg-surface-2/70 px-3 py-1.5">
                  <Terminal className="size-3.5 shrink-0 text-ml-ink" />
                  <span className="t-label text-foreground">What the agent sees</span>
                  <InfoTip label="About context">
                    The block of memory text assembled and handed to the model for this question.
                  </InfoTip>
                  <span className="ml-auto flex shrink-0 items-center gap-2">
                    <MiniMeter
                      value={usedPct / 100}
                      hex={usedPct > 90 ? 'var(--risk)' : 'var(--ml)'}
                      height={4}
                      className="w-12"
                    />
                    <span className="tabular font-mono text-[0.62rem] text-foreground">
                      {data.tokens_used} / {TOKEN_BUDGET} tokens
                    </span>
                  </span>
                </figcaption>
                <pre className="flex-1 overflow-auto p-3 font-mono text-[0.68rem] leading-relaxed whitespace-pre-wrap text-foreground">
                  {data.working_memory}
                </pre>
              </figure>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
