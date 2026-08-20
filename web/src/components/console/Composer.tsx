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
import { cn } from '@/lib/utils'
import type { Role } from '@/lib/stream'

import { AttachmentPicker } from './AttachmentPicker'
import { BudgetLine } from './BudgetLine'
import { questionWithAttachment, type TurnAttachment } from './composerAttachment'
import { ModeChips } from './ModeChips'
import { questionLength } from './questionLength'
import type { RunMode } from './runMode'

interface ComposerProps {
  role: Role
  personaId: string
  onPersonaChange: (id: string) => void
  /** The width this turn will be asked to run at. */
  mode: RunMode
  onModeChange: (mode: RunMode) => void
  /**
   * `hero` is the idle console: one large field under the wordmark, chips beneath it.
   * `docked` is the same control once a thread exists, compact at the foot of it.
   */
  variant?: 'hero' | 'docked'
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
/** The idle field is the page's one large surface, so it grows further before it scrolls. */
const HERO_MAX_HEIGHT = 260

/**
 * The composer — where a question is written, priced, and sent.
 *
 * Enter sends and Shift+Enter breaks a line, which is the contract every chat surface
 * has taught people to expect. The box locks while a run streams, because a chat with
 * two live runs in it has no way to say which events belong to which question.
 *
 * ## What the control row carries, and what it deliberately does not
 *
 * Width, persona, model and tools are four orthogonal axes. Three of them are here, and
 * the fourth is absent for a stated reason rather than by oversight — a control that
 * does not change the run is worse than one that is not there:
 *
 * - **Width** ships whole now. `QueryRequest` has carried `depth_mode` and
 *   `requested_fanout` since Phase 5 and `startRun` has always posted both, but nothing
 *   on screen could set them, so every turn went out as `null` and the fan-out was
 *   reachable only by luck of the classifier. The mode chips are the missing half. See
 *   {@link ModeChips}.
 * - **Persona** scopes the data and the tool roster, and is chosen beside the width.
 * - **Model** ships as a *report*, not a chooser. `GET /models` answers what the
 *   gateway would actually do, so the panel is true. Persisting a preference is
 *   `agent.model` in the settings catalogue, and that **does** have an HTTP surface now
 *   — `GET|PUT /settings/{key}` — which the Settings screen writes, with the badge that
 *   names the scope that decided. A second writer here, in a menu with no room for that
 *   badge, would reintroduce the ambiguity Settings exists to remove. See
 *   {@link ModelsMenu}.
 * - **Tools** is still absent, and this is the one that has no wire: `GET /tools`
 *   reports the effective roster and nothing accepts a pin for one run.
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
  mode,
  onModeChange,
  variant = 'docked',
  token,
  budgetKey,
  onSend,
  running,
}: ComposerProps): ReactElement {
  const hero = variant === 'hero'
  const [question, setQuestion] = useState('')
  const [attachment, setAttachment] = useState<TurnAttachment | null>(null)
  const boxRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = boxRef.current
    if (el === null) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, hero ? HERO_MAX_HEIGHT : MAX_HEIGHT)}px`
  }, [question, hero])

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
      className={cn(
        '@container/composer bg-card focus-within:border-ring',
        hero
          ? 'rounded-2xl border border-border p-3 shadow-card transition-shadow duration-[var(--dur-base)] focus-within:shadow-hover'
          : 'rounded-lg border border-border p-2.5',
      )}
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
        placeholder={hero ? 'Ask Aegis anything…' : 'Ask anything…'}
        aria-invalid={length.over}
        aria-describedby={length.showCounter ? 'composer-length' : undefined}
        className={cn(
          'w-full resize-none bg-transparent outline-none placeholder:text-muted-foreground',
          hero ? 'px-3 py-2.5 text-base leading-relaxed' : 'px-2 py-1.5 text-sm leading-relaxed',
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

      <div
        className={cn(
          'flex flex-wrap items-center gap-x-2 gap-y-1.5',
          hero ? 'px-1 pt-2' : 'pt-1.5',
        )}
      >
        <ModeChips
          role={role}
          mode={mode}
          onModeChange={onModeChange}
          personaId={personaId}
          onPersonaChange={onPersonaChange}
          token={token}
          compact={!hero}
        />

        <AttachmentPicker
          token={token}
          question={question}
          attachment={attachment}
          onAttach={setAttachment}
          onClear={() => setAttachment(null)}
          disabled={running}
        />

        <Button
          type="submit"
          size={hero ? 'default' : 'sm'}
          className="ml-auto"
          disabled={running || question.trim() === '' || length.over}
        >
          {running ? 'Sending' : 'Send'}
          <ArrowUp aria-hidden className="size-4" />
        </Button>
      </div>

      <div
        className={cn(
          'flex flex-wrap items-center gap-x-3 gap-y-1',
          hero ? 'px-1 pt-2' : 'pt-1.5',
        )}
      >
        <BudgetLine token={token} refreshKey={budgetKey} />
        {/* A container query, not `sm:`. The docked composer lives in a column that is
            about 560px wide at 1440, and a viewport breakpoint that fires at 640px of
            *browser* has no idea about that. */}
        <p className="hidden text-[0.72rem] text-muted-foreground @[26rem]/composer:block">
          Enter sends · Shift + Enter adds a line
        </p>
      </div>
    </form>
  )
}
