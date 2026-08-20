'use client'

import { Brain, MessagesSquare, Plus, ScanSearch, Wallet, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react'

import { EpisodicSessionsPanel } from '@/components/memory/EpisodicSessionsPanel'
import { MiniMeter } from '@/components/memory/MiniMeter'
import { RecallDebugPanel } from '@/components/memory/RecallDebugPanel'
import { SemanticFactsPanel } from '@/components/memory/SemanticFactsPanel'
import { ErrorRow, LoadingRow } from '@/components/memory/StateRow'
import { StructuredProfilePanel } from '@/components/memory/StructuredProfilePanel'
import { useAsync, type AsyncState } from '@/components/memory/useAsync'
import { isAuthFailure } from '@/lib/api/apiError'
import { getMemoryFacts, getMemoryProfile, getMemorySessions } from '@/lib/api/client'
import { getMyBudget, type MyBudgetResponse } from '@/lib/api/console'
import type { MemoryFactsResponse } from '@/lib/api/memory'
import { cn } from '@/lib/utils'

import {
  MAX_OPEN_CARDS,
  availableCards,
  cardSpec,
  cardToEvict,
  closeCard,
  initialOpenCards,
  memoryAvailable,
  openCard,
  railExplanation,
  visibleCards,
  type MemoryAccess,
  type MemoryCardId,
  type RailCapabilities,
} from './memoryCards'

/** The icon each card wears in its header and in the add menu. */
const CARD_ICONS: Record<MemoryCardId, typeof Brain> = {
  remembered: Brain,
  conversations: MessagesSquare,
  recall: ScanSearch,
  budget: Wallet,
}

/**
 * One card in the rail: a header outside the box, the panel inside it.
 *
 * `bare` is for a panel that already draws its own surface (the recall trace), so the
 * rail never stacks a card inside a card.
 */
function RailCard({
  id,
  title,
  onClose,
  bare = false,
  children,
}: {
  id: MemoryCardId
  title: string
  onClose: () => void
  bare?: boolean
  children: ReactElement
}): ReactElement {
  const Icon = CARD_ICONS[id]
  return (
    <section aria-label={title} className="flex min-w-0 flex-col gap-1.5">
      <header className="flex items-center gap-2">
        <Icon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
        <h3 className="t-label min-w-0 truncate text-foreground">{title}</h3>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto shrink-0 rounded-md p-1 text-muted-foreground outline-none transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X aria-hidden className="size-3.5" />
          <span className="sr-only">Close {title}</span>
        </button>
      </header>
      {bare ? (
        children
      ) : (
        <div className="rounded-xl border border-border bg-card px-4 py-3.5">
          {children}
        </div>
      )}
    </section>
  )
}

/**
 * "What I remember" — the durable facts, then the profile distilled from them.
 *
 * The facts read is handed in rather than started here, because the rail already made
 * it: the same request decides whether this card is offered at all. Firing it twice for
 * one card would be two round trips to answer one question.
 */
function RememberedBody({
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
    <div className="flex flex-col gap-4">
      <SemanticFactsPanel state={facts} />
      <div className="border-t border-border/70 pt-4">
        <StructuredProfilePanel state={profile.state} />
      </div>
    </div>
  )
}

/** "Past conversations" — the episodic sessions, newest summaries first. */
function ConversationsBody({
  token,
  subject,
}: {
  token: string | null
  subject: string
}): ReactElement {
  const sessions = useAsync(() => getMemorySessions(token, subject), [token, subject])
  if (sessions.state.status === 'loading') return <LoadingRow label="Loading conversations…" />
  if (sessions.state.status === 'error') return <ErrorRow message={sessions.state.message} />
  return <EpisodicSessionsPanel token={token} sessions={sessions.state.data.rows} />
}

/** "Budget" — the caller's own spend against the caller's own cap. */
function BudgetBody({ budget }: { budget: MyBudgetResponse }): ReactElement {
  const capped = budget.measured && budget.usd_cap != null
  if (!capped) {
    return (
      <p className="text-[0.8rem] leading-relaxed text-muted-foreground">
        No spend cap governs this account yet, so there is nothing to measure. Ask a tenant
        admin to set one under Governance.
      </p>
    )
  }
  const cap = budget.usd_cap ?? 0
  const used = budget.cost_usd_used
  return (
    <div className="flex flex-col gap-2">
      <p className="tabular font-mono text-lg text-foreground">
        ${used.toFixed(2)}
        <span className="ml-1 text-[0.8rem] text-muted-foreground">of ${cap.toFixed(2)}</span>
      </p>
      <MiniMeter value={cap > 0 ? used / cap : 0} hex="var(--risk)" height={5} />
      <p className="text-[0.72rem] text-muted-foreground">
        Read from the same rows the gateway compares every call against, so this figure and
        a refusal can never disagree.
      </p>
    </div>
  )
}

/** The add menu — the cards that are readable and not already open. */
function AddMenu({
  open,
  caps,
  onAdd,
}: {
  open: MemoryCardId[]
  caps: RailCapabilities
  onAdd: (id: MemoryCardId) => void
}): ReactElement | null {
  const [showing, setShowing] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  // Escape closes it, and so does a click anywhere else — a menu that only closes by
  // re-clicking its own button is a menu people leave open by accident.
  useEffect(() => {
    if (!showing) return
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setShowing(false)
    }
    const onDown = (event: MouseEvent): void => {
      if (!(event.target instanceof Node)) return
      if (boxRef.current?.contains(event.target) === true) return
      setShowing(false)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
    }
  }, [showing])

  const closed = availableCards(caps).filter((card) => !open.includes(card.id))
  if (closed.length === 0) return null

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        onClick={() => setShowing((was) => !was)}
        aria-expanded={showing}
        className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-[0.72rem] font-medium text-foreground outline-none transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Plus aria-hidden className="size-3.5" />
        Add a card
      </button>

      {showing && (
        <div className="absolute right-0 z-20 mt-1.5 w-72 max-w-[80vw] rounded-xl border border-border bg-card p-1.5 shadow-pop">
          <ul className="flex flex-col gap-0.5">
            {closed.map((card) => {
              const evicted = cardToEvict(open, card.id)
              const evictedTitle = evicted === null ? null : cardSpec(evicted)?.title
              const Icon = CARD_ICONS[card.id]
              return (
                <li key={card.id}>
                  <button
                    type="button"
                    onClick={() => {
                      onAdd(card.id)
                      setShowing(false)
                    }}
                    className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left outline-none transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    <span className="min-w-0">
                      <span className="block text-[0.8rem] font-medium text-foreground">
                        {card.title}
                      </span>
                      <span className="block text-[0.72rem] leading-snug text-muted-foreground">
                        {card.menuLine}
                      </span>
                      {evictedTitle != null && (
                        <span className="mt-0.5 block text-[0.7rem] leading-snug text-muted-foreground">
                          Three at a time — this closes “{evictedTitle}”.
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}

interface MemoryRailProps {
  token: string | null
  /** The `user:<id>` subject this sign-in owns, or null when memory is not scoped. */
  subject: string | null
}

/**
 * The memory rail — what the agent has learned, beside the conversation that taught it.
 *
 * Four cards exist and **three may be open at once**. The cap is enforced here rather
 * than left to restraint: opening a fourth closes the least-recently-opened one, and the
 * add menu names which before the click. It opens with exactly one card — "What I
 * remember" — and everything else is opt-in.
 *
 * A card is only offered when its reading is actually available, and that has to mean
 * *available*, not *plausible*. Two things can withhold the memory cards and both were
 * needed: the bearer resolving to no subject at all, and the store refusing the subject
 * it does have. Only the first was checked, so signing in through the console's own
 * "Enter as Client" button opened a card that rendered `Could not load. GET
 * /memory/facts?subject=user%3A5... failed: 403 Forbidden` — the exact thing the rail
 * claims never to do. The first read is now the probe as well as the card's content:
 * one request, and a refusal withdraws the offer instead of printing itself.
 *
 * A 500 or a dropped connection does **not** withhold anything. That reading is absent
 * right now, not absent for this person, and a card that disappears on a backend hiccup
 * teaches something false about what the agent knows.
 *
 * The panels themselves are the ones the Memory section already uses, reused whole
 * rather than reimplemented at a second size.
 */
export function MemoryRail({ token, subject }: MemoryRailProps): ReactElement {
  const [budget, setBudget] = useState<MyBudgetResponse | null>(null)
  const [open, setOpen] = useState<MemoryCardId[]>(() =>
    initialOpenCards({ memory: subject !== null, budget: false }),
  )

  // The rail's own read of the durable facts. It is the default card's content *and*
  // the probe that decides whether any memory card is offered — the same request,
  // because asking twice to answer one question is a round trip nobody needs.
  const facts = useAsync<MemoryFactsResponse>(
    () =>
      subject === null
        ? Promise.reject(new Error('This sign-in owns no memory subject.'))
        : getMemoryFacts(token, subject, true),
    [token, subject],
  )

  const access: MemoryAccess = useMemo(() => {
    if (subject === null) return 'unscoped'
    if (facts.state.status === 'loading') return 'probing'
    if (facts.state.status === 'ready') return 'readable'
    return isAuthFailure(facts.state.error) ? 'refused' : 'readable'
  }, [subject, facts.state])

  // One probe decides both halves of the budget card: whether to offer it, and what it
  // says. A refusal (no such route, or not for this role) simply withholds the card.
  useEffect(() => {
    let alive = true
    void getMyBudget(token)
      .then((data) => {
        if (alive) setBudget(data)
      })
      .catch(() => {
        if (alive) setBudget(null)
      })
    return () => {
      alive = false
    }
  }, [token])

  const caps: RailCapabilities = useMemo(
    () => ({ memory: memoryAvailable(access), budget: budget !== null }),
    [access, budget],
  )

  const shown = visibleCards(open, caps)
  const offered = availableCards(caps)
  const explanation = railExplanation(access, shown.length, offered.length)

  const add = (id: MemoryCardId): void => setOpen((current) => openCard(current, id))
  const drop = (id: MemoryCardId): void => setOpen((current) => closeCard(current, id))

  return (
    <aside aria-label="What the agent knows about you" className="flex min-w-0 flex-col gap-3">
      <header className="flex items-center gap-2">
        <h2 className="eyebrow min-w-0 truncate">What I know about you</h2>
        <span
          className={cn(
            'tabular ml-auto shrink-0 font-mono text-[0.66rem]',
            shown.length === MAX_OPEN_CARDS ? 'text-foreground' : 'text-muted-foreground',
          )}
          title={`${MAX_OPEN_CARDS} cards at a time`}
        >
          {shown.length}/{MAX_OPEN_CARDS}
        </span>
        <AddMenu open={shown} caps={caps} onAdd={add} />
      </header>

      {explanation !== null && (
        <p className="rounded-xl border border-dashed border-border bg-surface-2/40 px-4 py-3 text-[0.78rem] leading-relaxed text-muted-foreground">
          {explanation}
        </p>
      )}

      {shown.map((id) => {
        const spec = cardSpec(id)
        if (spec === null) return null
        if (id === 'remembered' && subject !== null) {
          return (
            <RailCard key={id} id={id} title={spec.title} onClose={() => drop(id)}>
              <RememberedBody token={token} subject={subject} facts={facts.state} />
            </RailCard>
          )
        }
        if (id === 'conversations' && subject !== null) {
          return (
            <RailCard key={id} id={id} title={spec.title} onClose={() => drop(id)}>
              <ConversationsBody token={token} subject={subject} />
            </RailCard>
          )
        }
        if (id === 'recall' && subject !== null) {
          return (
            <RailCard key={id} id={id} title={spec.title} onClose={() => drop(id)} bare>
              <RecallDebugPanel token={token} subject={subject} />
            </RailCard>
          )
        }
        if (id === 'budget' && budget !== null) {
          return (
            <RailCard key={id} id={id} title={spec.title} onClose={() => drop(id)}>
              <BudgetBody budget={budget} />
            </RailCard>
          )
        }
        return null
      })}
    </aside>
  )
}
