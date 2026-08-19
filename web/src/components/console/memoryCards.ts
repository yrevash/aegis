/**
 * The memory rail's card model — which cards exist, which are offered, and the cap.
 *
 * The rail answers one question the person asked for: *what has the agent learned about
 * me?* It is deliberately small. Three cards may be open at once and no more, because a
 * rail that grows without limit is the clutter the surface was asked not to become — so
 * the cap lives here, in code, rather than in the restraint of whoever opens the menu.
 *
 * `open` is ordered **least-recently-opened first**. Opening a fourth card drops the
 * head of that list rather than refusing the click: the add menu names the card that
 * will close before it happens ({@link cardToEvict}), so the trade is visible and the
 * person is never stuck hunting for something to close first.
 *
 * Pure and framework-free, so the cap can be tested without a renderer.
 */

/** The cards the rail can offer. */
export type MemoryCardId = 'remembered' | 'conversations' | 'recall' | 'budget'

/** What a card needs to be readable before it is worth offering. */
export type MemoryCardNeed = 'memory' | 'budget'

/** One card in the rail's catalogue. */
export interface MemoryCardSpec {
  id: MemoryCardId
  /** Sentence-case title, named for what the person gets. */
  title: string
  /** One line in the add menu: what opening this puts on screen. */
  menuLine: string
  /** The reading that has to work for this card to be offered at all. */
  needs: MemoryCardNeed
}

/** The catalogue, in the order the add menu lists it. */
export const MEMORY_CARDS: readonly MemoryCardSpec[] = [
  {
    id: 'remembered',
    title: 'What I remember',
    menuLine: 'The durable facts the agent kept, and the profile it built from them.',
    needs: 'memory',
  },
  {
    id: 'conversations',
    title: 'Past conversations',
    menuLine: 'Earlier sessions, each with its summary and its transcript.',
    needs: 'memory',
  },
  {
    id: 'recall',
    title: 'Why this answer',
    menuLine: 'Trace what a question pulls out of memory, and how it was ranked.',
    needs: 'memory',
  },
  {
    id: 'budget',
    title: 'Budget',
    menuLine: 'Your own spend against your own cap.',
    needs: 'budget',
  },
]

/** How many cards may be open at once. The design, not a suggestion. */
export const MAX_OPEN_CARDS = 3

/** What the rail opens with: one card, the one that was actually asked for. */
export const DEFAULT_OPEN_CARDS: readonly MemoryCardId[] = ['remembered']

/** What this sign-in can actually read, decided before a card is offered. */
export interface RailCapabilities {
  /** True when the bearer resolves to a memory subject the `/memory/*` store is keyed on. */
  memory: boolean
  /** True when `GET /me/budget` answered for this caller. */
  budget: boolean
}

/** Whether one card's reading is available. */
export function isCardAvailable(caps: RailCapabilities, id: MemoryCardId): boolean {
  const spec = MEMORY_CARDS.find((card) => card.id === id)
  if (spec === undefined) return false
  return spec.needs === 'budget' ? caps.budget : caps.memory
}

/**
 * The cards worth offering. A card whose reading is not there is not listed and not
 * opened — an empty panel over an endpoint that never answers teaches the wrong thing
 * about the agent's memory.
 */
export function availableCards(caps: RailCapabilities): MemoryCardSpec[] {
  return MEMORY_CARDS.filter((card) => isCardAvailable(caps, card.id))
}

/** The catalogue entry for an id, or null. */
export function cardSpec(id: MemoryCardId): MemoryCardSpec | null {
  return MEMORY_CARDS.find((card) => card.id === id) ?? null
}

/**
 * Which open card an add would close, or null when there is room (or the card is
 * already open). The add menu shows this so the cap is legible before the click.
 */
export function cardToEvict(
  open: readonly MemoryCardId[],
  id: MemoryCardId,
): MemoryCardId | null {
  if (open.includes(id) || open.length < MAX_OPEN_CARDS) return null
  return open[0] ?? null
}

/** Open a card, closing the least-recently-opened one when the rail is already full. */
export function openCard(open: readonly MemoryCardId[], id: MemoryCardId): MemoryCardId[] {
  if (open.includes(id)) return [...open]
  const next = [...open, id]
  return next.length > MAX_OPEN_CARDS ? next.slice(next.length - MAX_OPEN_CARDS) : next
}

/** Close a card. */
export function closeCard(open: readonly MemoryCardId[], id: MemoryCardId): MemoryCardId[] {
  return open.filter((entry) => entry !== id)
}

/** The rail's starting state: the default card, if this sign-in can read it. */
export function initialOpenCards(caps: RailCapabilities): MemoryCardId[] {
  return DEFAULT_OPEN_CARDS.filter((id) => isCardAvailable(caps, id))
}

/** Drop anything that is open but no longer readable, keeping the open order. */
export function visibleCards(
  open: readonly MemoryCardId[],
  caps: RailCapabilities,
): MemoryCardId[] {
  return open.filter((id) => isCardAvailable(caps, id))
}
