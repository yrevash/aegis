'use client'

import { ChevronDown, Sparkles } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { SemanticFactsPanel } from '@/components/memory/SemanticFactsPanel'
import { StructuredProfilePanel } from '@/components/memory/StructuredProfilePanel'
import { ErrorRow, LoadingRow } from '@/components/memory/StateRow'
import { useAsync, type AsyncState } from '@/components/memory/useAsync'
import { Figure } from '@/components/primitives/Figure'
import { getMemoryProfile } from '@/lib/api/client'
import { cn } from '@/lib/utils'
import type { MemoryFactRow, MemoryFactsResponse } from '@/lib/api/memory'
import type { MemoryEvent } from '@/lib/stream'

import { digestFacts, recallLine } from './factDigest'

/** One counted figure in the digest head: the number, then what it counts. */
function Count({ value, label }: { value: number; label: string }): ReactElement {
  return (
    <div className="min-w-0">
      <Figure size="stat" className="text-foreground">
        {value.toLocaleString('en-US')}
      </Figure>
      <span className="eyebrow mt-0.5 block truncate">{label}</span>
    </div>
  )
}

/**
 * One fact, as a line rather than a card.
 *
 * Two lines at most, and the recall count under it in mono. The full row — the
 * subject·predicate·object triple and the bitemporal window — is one layer down behind
 * "show all", where {@link SemanticFactsPanel} renders it as it always has.
 */
function FactLine({ fact }: { fact: MemoryFactRow }): ReactElement {
  return (
    <li className="flex min-w-0 gap-2 border-t border-border/70 py-2 first:border-t-0 first:pt-0">
      <span
        aria-hidden
        className="mt-1.5 size-1.5 shrink-0 rounded-full bg-blue-400"
      />
      <span className="min-w-0">
        <span className="line-clamp-2 text-[0.8rem] leading-snug text-foreground">
          {fact.text}
        </span>
        <span className="tabular mt-0.5 block font-mono text-[0.6875rem] text-muted-foreground">
          {fact.access_count}× recalled · {Math.round(fact.confidence * 100)}% confident
        </span>
      </span>
    </li>
  )
}

/**
 * What this turn actually pulled out of memory — the wire's own `memory` event.
 *
 * It is the rail's reason to be open *during* a run: the digest below says what the
 * agent holds, and this says what it reached for just now. Absent when the run carried
 * no `session_id`, because memory genuinely did not run then and "recalled nothing"
 * would be a different, false, claim.
 */
export function RecallReceipt({ memory }: { memory: MemoryEvent }): ReactElement {
  return (
    <section
      aria-label="Recalled into this turn"
      className="flex flex-col gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2.5"
    >
      <span className="eyebrow flex items-center gap-1.5">
        <Sparkles aria-hidden className="size-3.5 text-blue-700" />
        Recalled into this turn
      </span>
      <p className="text-[0.8rem] leading-snug text-foreground">
        <Figure className="text-blue-700">{memory.recalled_fact_count}</Figure> facts and{' '}
        <Figure className="text-blue-700">{memory.recalled_message_count}</Figure> earlier
        messages
      </p>
      <p className="tabular font-mono text-[0.6875rem] text-muted-foreground">
        {memory.tokens_used.toLocaleString('en-US')} tokens of working memory
      </p>
    </section>
  )
}

/**
 * "What I remember" — a count, the few facts the agent actually leans on, and the rest
 * one click away.
 *
 * ## Why this replaced the panel it used to render
 *
 * The rail used to mount {@link SemanticFactsPanel} whole. That panel renders **every**
 * row the store returns, each as its own bordered card with a confidence figure, a
 * recall count, a status badge and a disclosure — which is right in the Memory section,
 * where the facts are the content and the page is theirs. In a 20rem rail beside a
 * question box it made the console's idle state taller than the viewport with four
 * facts, and several viewports tall with a dozen. The owner's verdict was "full memory
 * being seen", and it was exactly that.
 *
 * A rail is context. So the default is a digest — how many beliefs are current, the
 * three the agent has recalled most, and a button that names how many more there are.
 * "Show all" mounts the real panel underneath, unchanged, so nothing is lost and the
 * expensive rendering is opt-in. The profile moves behind the same toggle: an
 * unconsolidated profile is four lines saying nothing has been consolidated, and that is
 * not worth the top of a rail.
 *
 * Every figure is the store's: `is_valid` counts the current beliefs, `access_count`
 * both ranks the digest and sums the recall total, and no number here is estimated.
 */
export function MemoryDigest({
  token,
  subject,
  facts,
}: {
  token: string | null
  subject: string
  facts: AsyncState<MemoryFactsResponse>
}): ReactElement {
  const [showAll, setShowAll] = useState(false)

  if (facts.status === 'loading') return <LoadingRow label="Loading what I remember…" />
  if (facts.status === 'error') return <ErrorRow message={facts.message} />

  const digest = digestFacts(facts.data.rows)
  const line = recallLine(digest)

  if (digest.current === 0 && digest.superseded === 0) {
    return (
      <p className="text-[0.8rem] leading-relaxed text-muted-foreground">
        Nothing has been kept about you yet. Facts are written back after a turn, so this
        fills in as you use the console.
      </p>
    )
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex items-start gap-6">
        <Count value={digest.current} label="facts held" />
        {digest.superseded > 0 && <Count value={digest.superseded} label="replaced" />}
      </div>

      {line !== null && (
        <p className="text-[0.72rem] leading-snug text-muted-foreground">{line}</p>
      )}

      {digest.top.length > 0 && (
        <ul className="flex min-w-0 flex-col">
          {digest.top.map((fact) => (
            <FactLine key={fact.id} fact={fact} />
          ))}
        </ul>
      )}

      <button
        type="button"
        onClick={() => setShowAll((was) => !was)}
        aria-expanded={showAll}
        className="flex items-center gap-1.5 self-start rounded-md text-[0.76rem] font-medium text-blue-700 outline-none transition-colors duration-[var(--dur-fast)] hover:text-blue-800 focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronDown
          aria-hidden
          className={cn('size-3.5 transition-transform', showAll && 'rotate-180')}
        />
        {showAll
          ? 'Show the summary'
          : digest.hidden > 0
            ? `Show all ${digest.current} facts and the profile`
            : 'Show the full record and the profile'}
      </button>

      {showAll && <FullRecord token={token} subject={subject} facts={facts} />}
    </div>
  )
}

/**
 * The rest of the record, mounted only once asked for.
 *
 * The profile read fires here rather than in the digest for the same reason the panel
 * does: a rail that opens closed should not spend a round trip on something nobody has
 * looked at.
 */
function FullRecord({
  token,
  subject,
  facts,
}: {
  token: string | null
  subject: string
  facts: AsyncState<MemoryFactsResponse>
}): ReactElement {
  const profile = useAsync(() => getMemoryProfile(token, subject), [token, subject])
  return (
    <div className="animate-reveal flex flex-col gap-4 border-t border-border pt-3">
      <SemanticFactsPanel state={facts} />
      <div className="border-t border-border/70 pt-4">
        <StructuredProfilePanel state={profile.state} />
      </div>
    </div>
  )
}
