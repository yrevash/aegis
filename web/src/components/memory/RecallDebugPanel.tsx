'use client'

import { ArrowRight, ChevronDown, MessagesSquare, Network, ScanSearch, Search, Terminal } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { getRecallDebug } from '@/lib/api/client'
import { SceneState } from '@/components/illustration/Scene'
import { RevealOnScroll } from '@/components/shared'
import { Badge } from '@/components/ui/Badge'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/primitives/button'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { cn } from '@/lib/utils'
import type { RecallDebugItem } from '@/lib/api/memory'

import { ErrorRow, LoadingRow } from './StateRow'
import { MiniMeter } from './MiniMeter'
import { useAsync } from './useAsync'
import { RECALL_DIMENSIONS, TOKEN_BUDGET, budgetPct, recallQuery, recallScores } from './memoryText'

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
        <span className="min-w-0 flex-1 text-xs leading-snug break-words text-foreground">
          {item.text}
        </span>
        <Figure
          label={`Match score ${item.score.toFixed(2)}`}
          className="shrink-0 text-[0.6875rem] leading-4 text-foreground"
        >
          {item.score.toFixed(2)}
        </Figure>
        <Badge tone={item.injected ? 'ok' : 'neutral'} className="shrink-0 text-[0.6875rem]">
          {item.injected ? 'used' : 'not used'}
        </Badge>
        <ChevronDown
          aria-hidden
          className={cn(
            'size-3 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none',
            open && 'rotate-180',
          )}
        />
      </button>
      {open && (
        <div className="animate-reveal grid grid-cols-3 gap-2.5 border-t border-border/60 px-3 py-2.5">
          {RECALL_DIMENSIONS.map((dim) => (
            <div key={dim.key}>
              <div className="mb-1 flex items-center gap-1">
                <span className="eyebrow text-[0.6875rem]">{dim.label}</span>
                <InfoTip label={dim.label} className="size-3">
                  {dim.hint}
                </InfoTip>
              </div>
              <MiniMeter
                value={scores[dim.key]}
                hex={dim.hex}
                height={4}
                label={`${dim.label} score`}
              />
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
        <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <h4 className="t-label text-foreground">{title}</h4>
        <span aria-hidden className={cn('size-2 shrink-0 rounded-full', dotClass)} />
        <Figure className="ml-auto text-[0.6875rem] leading-4 text-muted-foreground">
          {count} used
        </Figure>
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
  // No seeded query: a placeholder question would put words in the operator's
  // mouth and trace a recall nobody asked for.
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState<string | null>(null)
  // Nothing is traced until a question is asked. Firing on mount would run a recall for
  // the empty string and rank the result as if somebody had asked for it.
  const { state } = useAsync(
    () => (query === null ? Promise.resolve(null) : getRecallDebug(token, subject, query)),
    [token, subject, query],
  )

  const data = state.status === 'ready' ? state.data : null
  const usedPct = data ? budgetPct(data.tokens_used) : 0

  return (
    <Card className="overflow-hidden">
      <div className="flex w-full items-center gap-2 px-5 py-3.5 transition-colors hover:bg-surface-2/40">
        {/* The heading wraps the disclosure control, rather than sitting inside it:
            the accordion pattern keeps the title in the heading outline and still
            makes the whole row the target. */}
        <h3 className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="flex w-full items-center gap-2 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span
              aria-hidden
              className="grid size-6 shrink-0 place-items-center rounded-md bg-blue-200/12"
            >
              <ScanSearch className="size-3.5 text-blue-700" />
            </span>
            <span className="t-title min-w-0 text-pretty text-foreground">
              Why did it recall this?
            </span>
          </button>
        </h3>
        <InfoTip label="About recall">
          Which memories a question pulls in, ranked by match, recency and importance.
        </InfoTip>
        {/* No role chip. A literal `admin` Badge sat here and told an `ai_team` analyst
            that their own recall card belonged to somebody else. The panel is served to
            whoever the bearer authorises; it has no business labelling them. */}
        <ChevronDown
          aria-hidden
          className={cn(
            'size-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none',
            open && 'rotate-180',
          )}
        />
      </div>

      {open && (
        <div className="animate-reveal space-y-4 border-t border-border/70 px-5 py-4">
          <form
            className="flex flex-col gap-2 sm:flex-row"
            onSubmit={(e) => {
              e.preventDefault()
              // Never a fallback question. `draft.trim() || 'request status'` stood here
              // and traced a recall for words nobody typed — the very thing the
              // docstring three lines above says this panel refuses to do.
              setQuery(recallQuery(draft))
            }}
          >
            <div className="relative min-w-0 flex-1">
              <label htmlFor="recall-query" className="sr-only">
                Ask what the agent would recall
              </label>
              <Search
                aria-hidden
                className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
              />
              <input
                id="recall-query"
                name="recall-query"
                type="search"
                autoComplete="off"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Ask what the agent would recall…"
                className="h-9 w-full min-w-0 rounded-md border border-input bg-surface pr-3 pl-9 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring"
              />
            </div>
            <Button type="submit" className="shrink-0" disabled={recallQuery(draft) === null}>
              Show recall <ArrowRight aria-hidden />
            </Button>
          </form>

          {query === null && (
            <SceneState name="curious" size="md" className="py-2">
              <p className="text-sm font-medium text-foreground">Nothing traced yet</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Ask a question to see what it would recall.
              </p>
            </SceneState>
          )}
          {query !== null && state.status === 'loading' && (
            <LoadingRow label="Gathering memories…" />
          )}
          {state.status === 'error' && <ErrorRow message={state.message} />}

          {data && (
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="space-y-3">
                <RecallGroup
                  title="Facts"
                  icon={Network}
                  dotClass="bg-blue-400"
                  count={data.recalled_fact_count}
                  items={data.facts}
                />
                <RecallGroup
                  title="Sessions"
                  icon={MessagesSquare}
                  dotClass="bg-blue-200"
                  count={data.recalled_message_count}
                  items={data.episodic}
                />
              </div>

              {/* The glass-box payoff, framed as a deliberate artefact: a
                  captioned block with the context budget on its own header rule. */}
              <figure className="flex flex-col overflow-hidden rounded-lg border border-border bg-surface-2/40">
                <figcaption className="flex items-center gap-2 border-b border-border/70 bg-surface-2/70 px-3 py-1.5">
                  <Terminal className="size-3.5 shrink-0 text-blue-800" aria-hidden />
                  <span className="t-label text-foreground">What the agent sees</span>
                  <InfoTip label="About context">
                    The block of memory text assembled and handed to the model for this question.
                  </InfoTip>
                  <span className="ml-auto flex shrink-0 items-center gap-2">
                    <MiniMeter
                      value={usedPct / 100}
                      hex={usedPct > 90 ? 'var(--risk)' : 'var(--blue-100)'}
                      height={4}
                      className="w-12"
                      label="Context budget used"
                    />
                    <Figure className="text-[0.6875rem] leading-4 text-foreground">
                      {data.tokens_used} / {TOKEN_BUDGET} tokens
                    </Figure>
                  </span>
                </figcaption>
                <pre
                  translate="no"
                  className="min-w-0 flex-1 overflow-auto p-3 font-mono text-[0.6875rem] leading-relaxed break-words whitespace-pre-wrap text-foreground"
                >
                  {data.working_memory}
                </pre>
              </figure>
            </div>
          )}

          {data && <Receipt origin="GET /memory/recall_debug" />}
        </div>
      )}
    </Card>
  )
}
