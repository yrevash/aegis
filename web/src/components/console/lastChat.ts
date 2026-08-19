/**
 * Which conversation this browser was last looking at.
 *
 * A reload opened a fresh empty chat. The conversation the person was in the middle of
 * was still there — in the rail, one click away — but the screen said "Nothing has run
 * yet", which is the console telling somebody their work is gone when it is not. Every
 * accidental refresh, every projector hiccup, every navigation back into the console
 * cost a click and a moment of doubt.
 *
 * Only the **server** conversation id is kept. Local turn state is not restored and
 * should not be: the event log that produced an answer is not stored (`run_events` is
 * backlog), so a resumed chat renders the stored transcript, honestly labelled "from the
 * transcript", rather than a replay it cannot support.
 *
 * Every access is wrapped, because `localStorage` throws in a private window and on a
 * cross-origin iframe, and losing the resume is a smaller problem than a console that
 * will not mount.
 */

/** Where the id lives. Namespaced beside `aegis.session`. */
const STORAGE_KEY = 'aegis.console.chat'

/** The conversation this browser was last in, or `null`. */
export function rememberedChat(): string | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored === null || stored === '' ? null : stored
  } catch {
    return null
  }
}

/**
 * Remember the conversation now on screen, or forget it.
 *
 * @param serverId - The `chat_sessions.id` now open, or `null` for a chat that has not
 *   started one yet — an unstarted chat is not somewhere to come back to.
 */
export function rememberChat(serverId: string | null): void {
  try {
    if (serverId === null) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, serverId)
  } catch {
    // Non-fatal: this tab simply will not resume. The rail still lists everything.
  }
}
