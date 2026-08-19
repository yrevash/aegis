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
  /** True when the `/memory/*` store both has a subject for this bearer and answers for it. */
  memory: boolean
  /** True when `GET /me/budget` answered for this caller. */
  budget: boolean
}

/**
 * Why the memory cards are not on offer — the three answers, kept apart.
 *
 * `unscoped` is the bearer having no `user:<id>` at all. `refused` is the store
 * declining to serve the subject it does have, which is what "Enter as Client" produces
 * and what the rail used to render as `Could not load. GET
 * /memory/facts?subject=user%3A5... failed: 403 Forbidden` inside an open card. They
 * need different sentences because they have different fixes.
 */
export type MemoryAccess = 'unscoped' | 'refused' | 'probing' | 'readable'

/**
 * What the rail says when it is showing no cards, or `null` when it owes no explanation.
 *
 * Two bugs lived in the old version of this decision. It keyed the "not scoped to a
 * memory subject" line on `offered.length === 0`, so a session with a **readable budget
 * card** and no `userId` got "0/3 · Every card is closed. Add one…" — blaming the person
 * for closing cards that were never offered them. And it had no sentence at all for a
 * subject the store refuses, because that case used to be rendered inside a card as a
 * raw 403.
 *
 * @param access - What `/memory/*` will do for this bearer.
 * @param openCount - How many cards are actually on screen.
 * @param offeredCount - How many cards this sign-in could open.
 * @returns The line to render, or `null` when the rail has cards up and owes nothing.
 */
export function railExplanation(
  access: MemoryAccess,
  openCount: number,
  offeredCount: number,
): string | null {
  // Something is on screen; the rail is showing its answer and owes no note about it.
  if (openCount > 0) return null

  if (access === 'unscoped') {
    return 'This sign-in is not scoped to a memory subject, so there is nothing here to read. Sign in as a tenant user to see what the agent has learned.'
  }
  if (access === 'refused') {
    return 'This sign-in may not read the memory store, so its cards are not offered. Sign in as the user whose memory you want to see, or ask an admin for access.'
  }
  if (offeredCount === 0) {
    return 'Nothing here is readable with this sign-in yet.'
  }
  return 'Every card is closed. Add one to see what the agent has kept from your past conversations.'
}

/**
 * Whether the memory cards are worth offering for this access state.
 *
 * `probing` counts as available on purpose: the first read is in flight, the default
 * card opens on it and shows its own loading row, and withholding the card for the
 * length of one request would make the rail flicker on every load.
 */
export function memoryAvailable(access: MemoryAccess): boolean {
  return access === 'readable' || access === 'probing'
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
