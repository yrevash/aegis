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

import { AttachmentPicker } from './AttachmentPicker'
import { BudgetLine } from './BudgetLine'
import type { TurnAttachment } from './composerAttachment'
import { ModelsMenu } from './ModelsMenu'

interface ComposerProps {
  role: Role
  personaId: string
  onPersonaChange: (id: string) => void
  /** Bearer for the composer's own reads: the routing table and the caller's budget. */
  token: string | null
  /** Bumped when a run settles, so the budget line re-reads exactly then. */
  budgetKey: number
  /** Send the question and whatever is attached to it. Only called with non-empty text. */
  onSend: (question: string, attachment: TurnAttachment | null) => void
  /** True while a run is in flight — one run at a time. */
  running: boolean
}

/** Grow the box with the question, up to the point it starts scrolling. */
const MAX_HEIGHT = 168

/**
 * The composer — where a question is written, priced, and sent.
 *
 * Enter sends and Shift+Enter breaks a line, which is the contract every chat surface
 * has taught people to expect. The box locks while a run streams, because a chat with
 * two live runs in it has no way to say which events belong to which question.
 *
 * ## What the control row carries, and what it deliberately does not
 *
 * Depth, model and tools are three orthogonal axes and the design calls for three
 * controls. Two of the three have nowhere to write to yet, and a control that does not
 * change the run is worse than one that is not there:
 *
 * - **Model** ships as a *report*, not a chooser. `GET /models` answers what the
 *   gateway would actually do, so the panel is true. Persisting a preference is
 *   `agent.model` in the settings catalogue — which exists, resolves platform → tenant
 *   → user, and has **no HTTP surface**, so there is nothing to PUT a choice to and
 *   nothing to read its `source` back from. See {@link ModelsMenu}.
 * - **Mode** and **Tools** are not here. `aegis.agent.run_agent` takes `depth_mode` and
 *   `requested_fanout`, and honours an explicit width exactly — but `QueryRequest`
 *   carries neither field and `POST /query` never passes one, so a mode picked here
 *   could not reach the run by any route. Adding the dropdown before the field would
 *   put a control on screen that silently does nothing.
 * - **Image** ships whole, because `POST /attachments` exists and the screened
 *   descriptor it returns is text the question can carry. See {@link AttachmentPicker}.
 */
export function Composer({
  role,
  personaId,
  onPersonaChange,
  token,
  budgetKey,
  onSend,
  running,
}: ComposerProps): ReactElement {
  const personas = personasForRole(role)
  const persona = getPersona(personaId) ?? personas[0]
  const [question, setQuestion] = useState('')
  const [attachment, setAttachment] = useState<TurnAttachment | null>(null)
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
    onSend(trimmed, attachment)
    setQuestion('')
    // The attachment belonged to that question. Carrying it into the next one would
    // re-attach an image the person has already used without them asking.
    setAttachment(null)
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
      <div className="flex flex-wrap items-center gap-2 pb-2">
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

        <ModelsMenu token={token} />

        <AttachmentPicker
          token={token}
          question={question}
          attachment={attachment}
          onAttach={setAttachment}
          onClear={() => setAttachment(null)}
          disabled={running}
        />
      </div>

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

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pt-1.5">
        <BudgetLine token={token} refreshKey={budgetKey} />

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
