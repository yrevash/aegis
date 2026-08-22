'use client'

import { MessageSquarePlus, PanelsTopLeft } from 'lucide-react'
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
} from 'react'

import { Button } from '@/components/primitives/button'
import { cn } from '@/lib/utils'

import type { ChatSession } from './threadReducer'

interface SessionRailProps {
  sessions: ChatSession[]
  activeId: string
  onSelect: (sessionId: string) => void
  onNew: () => void
}

/** The title a chat reads under. Empty means it has not been asked anything yet. */
function titleOf(session: ChatSession | undefined): string {
  if (session === undefined) return 'New chat'
  return session.title === '' ? 'New chat' : session.title
}

/**
 * The session list — every chat started in this tab.
 *
 * A chat is titled by its first question, because that is the only title anybody has
 * given it. An empty chat shows as "New chat" and there is only ever one of those: a
 * list full of untitled duplicates is worse than no list.
 *
 * The list is the caller's own `GET /sessions` list — real `chat_sessions` rows under
 * the tenant's RLS policy — merged with whatever this tab has started. A chat becomes a
 * stored row on its first question, not on the click that opened it, so an empty chat
 * never becomes clutter somebody has to delete.
 */
export function SessionRail({
  sessions,
  activeId,
  onSelect,
  onNew,
}: SessionRailProps): ReactElement {
  return (
    <nav aria-label="Chats" className="flex min-h-0 flex-col gap-2">
      <Button variant="outline" size="sm" onClick={onNew} className="justify-start">
        <MessageSquarePlus aria-hidden className="size-4" />
        New chat
      </Button>

      <ul className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
        {sessions.map((session) => {
          const active = session.id === activeId
          return (
            <li key={session.id}>
              <button
                type="button"
                onClick={() => onSelect(session.id)}
                aria-current={active ? 'true' : undefined}
                className={cn(
                  'w-full truncate rounded-md px-2.5 py-2 text-left text-[0.8rem] transition-colors',
                  'outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  active
                    ? 'bg-surface-2 font-medium text-foreground'
                    : 'text-muted-foreground hover:bg-surface-2/60 hover:text-foreground',
                )}
              >
                {titleOf(session)}
                {session.turns.length > 0 && (
                  <span className="ml-1.5 font-mono text-[0.66rem] text-muted-foreground/70">
                    {session.turns.length}
                  </span>
                )}
              </button>
            </li>
          )
        })}
      </ul>

      <p className="border-t border-border pt-2 text-[0.7rem] leading-snug text-muted-foreground">
        A chat is saved once you send its first question.
      </p>
    </nav>
  )
}

/** Everything inside the panel that can take focus, for the trap while it is open. */
const FOCUSABLE = 'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'

/**
 * The chat list, behind one control in the console header.
 *
 * ## Why it is no longer a column
 *
 * It was a permanent `13rem` grid track at every desktop width, so the thread — the
 * thing the console is *for* — was never in the middle of the screen: it sat pushed
 * right by a list of chat titles that a person looks at perhaps twice a session. Worse,
 * the same list had three different mental models on one screen — a rail above `lg`, a
 * `<select>` below it, and neither of them where the eye goes first.
 *
 * One control now, at every width. The trigger names the chat you are in, so the
 * current session is still stated while the list is closed, and **New chat** stays its
 * own header button rather than moving one click further away.
 *
 * ## Why the focus handling is written out
 *
 * The panel overlays the thread, so it owes the same contract every other overlay on
 * this screen honours: `aria-expanded` and `aria-controls` on the trigger, Escape to
 * close, focus returned to the trigger it came from, and a trap while it is open so Tab
 * cannot walk out of a panel that is covering what it walks into. A click outside closes
 * it without stealing focus, because that person is already going somewhere else.
 */
export function SessionMenu({
  sessions,
  activeId,
  onSelect,
  onNew,
}: SessionRailProps): ReactElement {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const close = useCallback((restoreFocus: boolean): void => {
    setOpen(false)
    if (restoreFocus) triggerRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        close(true)
        return
      }
      if (event.key !== 'Tab') return
      const panel = panelRef.current
      if (panel === null) return
      const focusable = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)]
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !panel.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }
    const onDown = (event: MouseEvent): void => {
      if (!(event.target instanceof Node)) return
      if (panelRef.current?.contains(event.target) === true) return
      if (triggerRef.current?.contains(event.target) === true) return
      setOpen(false)
    }
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('mousedown', onDown)
    }
  }, [open, close])

  // Into the panel on open, so the keyboard lands where the eye does.
  useEffect(() => {
    if (!open) return
    panelRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus()
  }, [open])

  const active = sessions.find((session) => session.id === activeId)
  const current = titleOf(active)
  const named = active !== undefined && active.title !== ''

  return (
    <div className="relative flex min-w-0 items-center gap-1.5">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? close(true) : setOpen(true))}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`Chats — ${sessions.length} in this tab. Current: ${current}`}
        className={cn(
          'inline-flex min-w-0 items-center gap-1.5 rounded-full border px-3 py-1 text-[0.78rem] font-medium outline-none transition-colors duration-[var(--dur-fast)] focus-visible:ring-2 focus-visible:ring-ring',
          open
            ? 'border-blue-200 bg-blue-50 text-blue-700'
            : 'border-border bg-surface/70 text-muted-foreground hover:border-blue-200 hover:text-blue-700',
        )}
      >
        <PanelsTopLeft aria-hidden className="size-3.5 shrink-0" />
        {/* The chat you are in, so the current session is stated while the list is
            closed. An untitled chat has no name to state — it would read as a second
            "New chat" button beside the real one — so it names the list instead. */}
        <span className="max-w-[9rem] truncate sm:max-w-[14rem]">
          {named ? current : 'Chats'}
        </span>
        {!named && sessions.length > 1 && (
          <span className="tabular font-mono text-[0.72rem]">{sessions.length}</span>
        )}
      </button>

      <Button variant="outline" size="sm" onClick={onNew} className="shrink-0">
        <MessageSquarePlus aria-hidden className="size-4" />
        New chat
      </Button>

      {open && (
        <div
          ref={panelRef}
          id={panelId}
          role="dialog"
          aria-modal="true"
          aria-label="Chats"
          /* `max-h` rather than a height: the frame it opens inside is `overflow-hidden`,
             so a list of forty chats would be clipped rather than scrolled. */
          className="absolute top-full left-0 z-30 mt-1.5 flex max-h-[60vh] w-[17rem] flex-col rounded-lg border border-border bg-card p-2 shadow-pop motion-safe:animate-trace-in"
        >
          <SessionRail
            sessions={sessions}
            activeId={activeId}
            onSelect={(id) => {
              onSelect(id)
              close(true)
            }}
            onNew={() => {
              onNew()
              close(true)
            }}
          />
        </div>
      )}
    </div>
  )
}
