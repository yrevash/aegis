'use client'

import { MessageSquarePlus } from 'lucide-react'
import type { ReactElement } from 'react'

import { Button } from '@/components/primitives/button'
import { cn } from '@/lib/utils'

import type { ChatSession } from './threadReducer'

interface SessionRailProps {
  sessions: ChatSession[]
  activeId: string
  onSelect: (sessionId: string) => void
  onNew: () => void
}

/**
 * The session rail — every chat started in this tab.
 *
 * A chat is titled by its first question, because that is the only title anybody has
 * given it. An empty chat shows as "New chat" and there is only ever one of those: a
 * rail full of untitled duplicates is worse than no rail.
 *
 * The rail is the caller's own `GET /sessions` list — real `chat_sessions` rows under
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
    <nav aria-label="Chats" className="flex h-full min-h-0 flex-col gap-2">
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
                  'outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
                  active
                    ? 'bg-surface-2 font-medium text-foreground'
                    : 'text-muted-foreground hover:bg-surface-2/60 hover:text-foreground',
                )}
              >
                {session.title === '' ? 'New chat' : session.title}
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
