'use client'

import {
  Brain,
  MessagesSquare,
  PanelRightClose,
  Plus,
  ScanSearch,
  Wallet,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react'

import { EpisodicSessionsPanel } from '@/components/memory/EpisodicSessionsPanel'
import { MiniMeter } from '@/components/memory/MiniMeter'
import { RecallDebugPanel } from '@/components/memory/RecallDebugPanel'
import { ErrorRow, LoadingRow } from '@/components/memory/StateRow'
import { useAsync, type AsyncState } from '@/components/memory/useAsync'
import { isAuthFailure } from '@/lib/api/apiError'
import { getMemorySessions } from '@/lib/api/client'
import { getMyBudget, type MyBudgetResponse } from '@/lib/api/console'
import type { MemoryFactsResponse } from '@/lib/api/memory'
import type { MemoryEvent } from '@/lib/stream'
import { cn } from '@/lib/utils'

import { MemoryDigest, RecallReceipt } from './MemoryDigest'
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

/**
 * What `/memory/*` will do for this bearer, read off the one facts request.
 *
 * A 403 withdraws the offer; a 500 or a dropped connection does not, because that
 * reading is absent *right now*, not absent for this person, and a card that disappears
 * on a backend hiccup teaches something false about what the agent knows.
 */
export function memoryAccessOf(
  subject: string | null,
  facts: AsyncState<MemoryFactsResponse>,
): MemoryAccess {
  if (subject === null) return 'unscoped'
  if (facts.status === 'loading') return 'probing'
  if (facts.status === 'ready') return 'readable'
  return isAuthFailure(facts.error) ? 'refused' : 'readable'
}

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
        <div className="rounded-lg border border-border bg-card px-4 py-3.5">
          {children}
        </div>
      )}
    </section>
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
        <div className="absolute right-0 z-20 mt-1.5 w-72 max-w-[80vw] rounded-lg border border-border bg-card p-1.5 shadow-pop">
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
  /**
   * The rail's read of `GET /memory/facts`, owned by the console.
   *
   * It is lifted out of this component because the console's header chip states the
   * count while the rail is closed, and two reads of one endpoint to answer one
   * question is a round trip nobody needs. It is still both the default card's content
   * *and* the probe that decides whether any memory card is offered.
   */
  facts: AsyncState<MemoryFactsResponse>
  /**
   * The newest run's own `memory` event, or null.
   *
   * Null is the honest default and means recall did not run — every turn without a
   * `session_id`. The receipt is withheld rather than rendered as zeros, because
   * "recalled nothing" and "memory never ran" are different facts.
   */
  turnRecall?: MemoryEvent | null
  /** Close the rail. The console owns whether it is on screen at all. */
  onClose?: () => void
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
 * ## What the default card shows, and what it stopped showing
 *
 * "What I remember" is a **digest** now — a count, the three facts the agent has
 * actually recalled most, and a button that names how many more there are. It used to
 * mount the Memory section's full facts panel, which renders every row expanded; four
 * facts made the idle console taller than a 900px screen and a dozen made it scroll for
 * several viewports with no run on it. The full record is one click down, unchanged.
 * See {@link MemoryDigest}.
 *
 * The other panels are still the Memory section's own, reused whole rather than
 * reimplemented at a second size.
 */
export function MemoryRail({
  token,
  subject,
  facts,
  turnRecall = null,
  onClose,
}: MemoryRailProps): ReactElement {
  const [budget, setBudget] = useState<MyBudgetResponse | null>(null)
  const [open, setOpen] = useState<MemoryCardId[]>(() =>
    initialOpenCards({ memory: subject !== null, budget: false }),
  )

  const access: MemoryAccess = memoryAccessOf(subject, facts)

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
    <aside
      aria-label="What the agent knows about you"
      className="flex min-w-0 flex-col gap-3"
    >
      <header className="flex items-center gap-2">
        {/* "What I know", not "What I know about you". The rail is 20rem wide and the
            header has to hold a title, the cap, an add menu and a close — the longer
            title pushed "Add a card" onto two lines. The `about you` is carried by the
            content, and by the console chip that opens this. */}
        <h2 className="eyebrow min-w-0 truncate">What I know</h2>
        {/* The cap, stated only when it binds. `1/3` on a rail with one card up is a
            number nobody needed. */}
        <span
          className={cn(
            'ml-auto shrink-0',
            shown.length === MAX_OPEN_CARDS
              ? 'tabular font-mono text-[0.6875rem] text-foreground'
              : 'sr-only',
          )}
          title={`${MAX_OPEN_CARDS} cards at a time`}
        >
          {shown.length}/{MAX_OPEN_CARDS}
        </span>
        <AddMenu open={shown} caps={caps} onAdd={add} />
        {onClose !== undefined && (
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-md p-1 text-muted-foreground outline-none transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            <PanelRightClose aria-hidden className="size-4" />
            <span className="sr-only">Hide what I know about you</span>
          </button>
        )}
      </header>

      {/* What the newest run actually reached for. Above the digest because it is about
          this turn, and the digest is about the record as a whole. */}
      {turnRecall !== null && <RecallReceipt memory={turnRecall} />}

      {explanation !== null && (
        <p className="rounded-lg border border-dashed border-border bg-surface-2/40 px-4 py-3 text-[0.78rem] leading-relaxed text-muted-foreground">
          {explanation}
        </p>
      )}

      {shown.map((id) => {
        const spec = cardSpec(id)
        if (spec === null) return null
        if (id === 'remembered' && subject !== null) {
          return (
            <RailCard key={id} id={id} title={spec.title} onClose={() => drop(id)}>
              <MemoryDigest token={token} subject={subject} facts={facts} />
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
