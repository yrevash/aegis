'use client'

import { ArrowUp, Loader2 } from 'lucide-react'
import {
  useCallback,
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
  /**
   * Bump to hand the caret back to the box from somewhere else on the console — after
   * an approval gate resolves, say, where the decision took focus and the person's next
   * act is another question. Optional: the composer already refocuses itself on mount,
   * after a send and when a run settles. Desktop-only, like every other focus move here.
   */
  focusKey?: number
}

/** Grow the box with the question, up to the point it starts scrolling. */
const MAX_HEIGHT = 168
/** The idle field is the page's one large surface, so it grows further before it scrolls. */
const HERO_MAX_HEIGHT = 260

/**
 * Where a programmatic focus is welcome.
 *
 * `autoFocus` on a phone raises the software keyboard over the page the moment the
 * console opens, which hides the thing the person came to read and is why this is a
 * media query rather than a prop. `pointer: fine` rather than width alone, so a tablet
 * held in landscape at 1024px is not treated as a laptop.
 */
const DESKTOP = '(min-width: 64rem) and (pointer: fine)'

/**
 * The composer — where a question is written, priced, and sent.
 *
 * Enter sends and Shift+Enter breaks a line, which is the contract every chat surface
 * has taught people to expect.
 *
 * ## What the control row carries, and what it deliberately does not
 *
 * Width, persona, model and tools are four orthogonal axes. Three of them are here, and
 * the fourth is absent for a stated reason rather than by oversight — a control that
 * does not change the run is worse than one that is not there:
 *
 * - **Width** ships whole. `QueryRequest` has carried `depth_mode` and
 *   `requested_fanout` since Phase 5 and `startRun` has always posted both, but nothing
 *   on screen could set them, so every turn went out as `null`. See {@link ModeChips}.
 * - **Persona** scopes the data and the tool roster, and is chosen beside the width.
 * - **Model** ships as a *report*, not a chooser: `GET /models` answers what the gateway
 *   would actually do. Persisting a preference is `agent.model` in Settings, which is
 *   the one place that can also say which scope decided. See {@link ModelsMenu}.
 * - **Tools** is still absent, and this is the one that has no wire: `GET /tools`
 *   reports the effective roster and nothing accepts a pin for one run.
 * - **Image** ships whole, because `POST /attachments` exists and the screened
 *   descriptor it returns is text the question can carry. See {@link AttachmentPicker}.
 *
 * ## One band under the box, not three
 *
 * The controls, the budget and a sentence explaining the Enter key used to be three
 * stacked rows under a field that is the whole point of the screen — so the thing you
 * type into was outweighed by its own footnotes. They are one band now: the three quiet
 * controls and the image picker on the left, the caller's spend and the one filled
 * button on the right. The keyboard sentence is **deleted from the page** and kept as
 * the field's own `aria-describedby`, where it is read once on focus by the people who
 * cannot see the box grow when they press Shift+Enter, rather than sitting under it
 * permanently for everyone else.
 *
 * ## Focus, which this component used not to touch at all
 *
 * There was no programmatic focus anywhere on the console: opening it left the caret
 * nowhere, sending a question left it on a button that had just gone quiet, and
 * resolving an approval left it inside a card that no longer existed. Focus comes back
 * to the box at each of those three moments, and only on a desktop pointer — see
 * {@link DESKTOP}.
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
  focusKey,
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

  /** Put the caret back in the box, on a desktop pointer only. */
  const takeFocus = useCallback(() => {
    const el = boxRef.current
    if (el === null) return
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    if (!window.matchMedia(DESKTOP).matches) return
    el.focus()
  }, [])

  // Opening the console, and any explicit request from the console around it.
  useEffect(() => {
    takeFocus()
  }, [takeFocus, focusKey])

  // A run settling. The gate, the decision card and the Send button have all had focus
  // by now; the next thing the person does is type.
  const wasRunning = useRef(running)
  useEffect(() => {
    if (wasRunning.current && !running) takeFocus()
    wasRunning.current = running
  }, [running, takeFocus])

  // What the rail will actually measure: the question plus any screened description.
  const length = questionLength(questionWithAttachment(question.trim(), attachment))
  const describedBy = ['composer-keys', length.showCounter ? 'composer-length' : null]
    .filter((id): id is string => id !== null)
    .join(' ')

  const send = (): void => {
    const trimmed = question.trim()
    if (running) return
    // Not a disabled button: a control that is greyed out gives no reason, and both
    // reasons here are fixed in the box the caret is being handed back to. The
    // over-length case already has the counter open and `aria-invalid` set.
    if (trimmed === '' || length.over) {
      boxRef.current?.focus()
      return
    }
    onSend(trimmed, attachment)
    setQuestion('')
    // The attachment belonged to that question. Carrying it into the next one would
    // re-attach an image the person has already used without them asking.
    setAttachment(null)
    takeFocus()
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
      aria-busy={running}
      className={cn(
        '@container/composer bg-card focus-within:border-ring',
        'focus-within:ring-2 focus-within:ring-ring/25',
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
        aria-describedby={describedBy}
        className={cn(
          'w-full resize-none bg-transparent outline-none placeholder:text-muted-foreground',
          hero ? 'px-3 py-2.5 text-base leading-relaxed' : 'px-2 py-1.5 text-sm leading-relaxed',
        )}
      />

      {/* Deleted from the page, kept for the people it is actually news to. */}
      <p id="composer-keys" className="sr-only">
        Enter sends the question. Shift and Enter together add a line.
      </p>

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

      {/* One band: the quiet controls, then the caller's spend and the one filled
          button. It wraps rather than scrolling, and every child carries `min-w-0`, so
          a long model name shortens the chip instead of widening the composer. */}
      <div
        className={cn(
          'flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5',
          hero ? 'px-1 pt-2.5' : 'pt-2',
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

        <div className="ml-auto flex min-w-0 items-center gap-2">
          <BudgetLine token={token} refreshKey={budgetKey} />
          <Button
            type="submit"
            size={hero ? 'default' : 'sm'}
            className="shrink-0"
            // Enabled until the request starts. Empty and over-length are answered in
            // the box, not by a control that has gone grey without saying why.
            disabled={running}
          >
            {running ? 'Sending' : 'Send'}
            {running ? (
              <Loader2 aria-hidden className="size-4 motion-safe:animate-spin" />
            ) : (
              <ArrowUp aria-hidden className="size-4" />
            )}
          </Button>
        </div>
      </div>
    </form>
  )
}
