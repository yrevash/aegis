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
import { questionWithAttachment, type TurnAttachment } from './composerAttachment'
import { ModelsMenu } from './ModelsMenu'
import { questionLength } from './questionLength'

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
 *   `agent.model` in the settings catalogue, and that **does** have an HTTP surface now
 *   — `GET|PUT /settings/{key}` — which the Settings screen writes, with the badge that
 *   names the scope that decided. A second writer here, in a menu with no room for that
 *   badge, would reintroduce the ambiguity Settings exists to remove. See
 *   {@link ModelsMenu}.
 * - **Mode** and **Tools** are not here — but no longer because the wire cannot carry
 *   them. `QueryRequest` carries `depth_mode` and `requested_fanout` now, and
 *   {@link startRun} already sends both (as `null` while nothing sets them), so the
 *   remaining work is a control and a piece of state, not a backend field. What is
 *   still missing for **Tools** is a per-run roster field; `GET /tools` reports the
 *   effective roster and nothing accepts a pin for one run.
 * - **Image** ships whole, because `POST /attachments` exists and the screened
 *   descriptor it returns is text the question can carry. See {@link AttachmentPicker}.
 *
 * ## The length cap
 *
 * The input rail refuses over 8,000 characters, so the composer stops there rather than
 * letting a 60,000-character paste be accepted, titled, rendered and *then* refused.
 * What is measured is the composed query — an attached image's screened description is
 * text on the same wire. See {@link questionLength}.
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

  // What the rail will actually measure: the question plus any screened description.
  const length = questionLength(questionWithAttachment(question.trim(), attachment))

  const send = (): void => {
    const trimmed = question.trim()
    if (trimmed === '' || running || length.over) return
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
      className="rounded-lg border border-border bg-card p-2.5 focus-within:border-ring"
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
              className="h-8 rounded-md border border-input bg-surface/60 px-2 text-[0.78rem] outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
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
        aria-invalid={length.over}
        aria-describedby={length.showCounter ? 'composer-length' : undefined}
        className={cn(
          'w-full resize-none bg-transparent px-2 py-1.5 text-sm leading-relaxed outline-none',
          'placeholder:text-muted-foreground',
        )}
      />

      {length.showCounter && (
        <p
          id="composer-length"
          // Announced, not shouted: a person pasting a long document should hear the
          // limit once they are near it, not have it read out on every keystroke.
          aria-live="polite"
          className={cn(
            'tabular px-2 pt-0.5 font-mono text-[0.7rem]',
            length.over ? 'text-block-ink' : 'text-muted-foreground',
          )}
        >
          {length.label}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pt-1.5">
        <BudgetLine token={token} refreshKey={budgetKey} />

        <p className="hidden text-[0.72rem] text-muted-foreground sm:block">
          Enter sends · Shift + Enter adds a line
        </p>

        <Button
          type="submit"
          size="sm"
          className="ml-auto"
          disabled={running || question.trim() === '' || length.over}
        >
          {running ? 'Sending' : 'Send'}
          <ArrowUp aria-hidden className="size-4" />
        </Button>
      </div>
    </form>
  )
}
