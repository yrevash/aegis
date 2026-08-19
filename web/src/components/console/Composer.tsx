'use client'

import { ArrowUp } from 'lucide-react'
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactElement,
} from 'react'

import { Button } from '@/components/primitives/button'
import { getPersona, personasForRole } from '@/config/personas'
import { cn } from '@/lib/utils'
import type { Role } from '@/lib/stream'

interface ComposerProps {
  role: Role
  personaId: string
  onPersonaChange: (id: string) => void
  /** Send the question. Only called with non-empty text. */
  onSend: (question: string) => void
  /** True while a run is in flight — one run at a time. */
  running: boolean
}

/** Grow the box with the question, up to the point it starts scrolling. */
const MAX_HEIGHT = 168

/**
 * The composer — where a question is written and sent.
 *
 * Enter sends and Shift+Enter breaks a line, which is the contract every chat surface
 * has taught people to expect. The box locks while a run streams, because a chat with
 * two live runs in it has no way to say which events belong to which question.
 *
 * Mode, model and tool controls are task 6.5 and are deliberately not stubbed here: a
 * control that does not change the run is worse than one that is not there yet.
 */
export function Composer({
  role,
  personaId,
  onPersonaChange,
  onSend,
  running,
}: ComposerProps): ReactElement {
  const personas = personasForRole(role)
  const persona = getPersona(personaId) ?? personas[0]
  const [question, setQuestion] = useState('')
  const boxRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = boxRef.current
    if (el === null) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`
  }, [question])

  const send = (): void => {
    const trimmed = question.trim()
    if (trimmed === '' || running) return
    onSend(trimmed)
    setQuestion('')
  }

  const submit = (event: FormEvent): void => {
    event.preventDefault()
    send()
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      send()
    }
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-2xl border border-border bg-card p-2.5 shadow-card focus-within:border-ring"
    >
      <label htmlFor="composer-question" className="sr-only">
        Your question
      </label>
      <textarea
        id="composer-question"
        ref={boxRef}
        rows={1}
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask anything…"
        className={cn(
          'w-full resize-none bg-transparent px-2 py-1.5 text-sm leading-relaxed outline-none',
          'placeholder:text-muted-foreground',
        )}
      />

      <div className="flex flex-wrap items-center gap-2 pt-1.5">
        {personas.length > 1 && (
          <>
            <label htmlFor="composer-persona" className="sr-only">
              Persona
            </label>
            <select
              id="composer-persona"
              value={persona?.id}
              onChange={(event) => onPersonaChange(event.target.value)}
              className="h-8 rounded-md border border-input bg-surface/60 px-2 text-[0.78rem] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/40"
            >
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </>
        )}

        <p className="hidden text-[0.72rem] text-muted-foreground sm:block">
          Enter sends · Shift + Enter adds a line
        </p>

        <Button
          type="submit"
          size="sm"
          className="ml-auto"
          disabled={running || question.trim() === ''}
        >
          {running ? 'Sending' : 'Send'}
          <ArrowUp aria-hidden className="size-4" />
        </Button>
      </div>
    </form>
  )
}
