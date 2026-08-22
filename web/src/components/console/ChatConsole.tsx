'use client'

import { BookOpen, Brain, ShieldAlert, ShieldCheck, Workflow } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'

import { ApprovalCard } from '@/components/approval/ApprovalCard'
import { SkillsDrawer } from '@/components/skills/SkillsDrawer'
import { draftFromRun } from '@/components/skills/skillDraft'
import { AegisLockup } from '@/components/brand/AegisLockup'
import { Badge } from '@/components/ui/Badge'
import { useAsync } from '@/components/memory/useAsync'
import { getGraph, getMemoryFacts } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import { getPersona, personasForRole } from '@/config/personas'
import { useMetrics } from '@/state/useMetrics'
import type { MemoryFactsResponse } from '@/lib/api/memory'
import type { ApprovalDecision, GraphResponse, MetricsResponse } from '@/lib/api/types'
import type { Role } from '@/lib/stream'
import type { RunState } from '@/state/runReducer'

import { LaneBoard } from './LaneBoard'
import { ApprovalSpotlight } from './ApprovalSpotlight'
import { AssistantBot } from './AssistantBot'
import { Composer } from './Composer'
import { attachmentVerdict, type TurnAttachment } from './composerAttachment'
import { RequestedWidth } from './DecisionStrip'
import { FlowCanvas } from './FlowCanvas'
import { MemoryRail, memoryAccessOf } from './MemoryRail'
import { memorySubjectOf } from './memorySubject'
import { RunPreview } from './RunPreview'
import { digestFacts } from './factDigest'
import { memoryAvailable } from './memoryCards'
import { beatFromSignal } from './motion'
import { AnswerBlock, ResultTabs, type ResultTabId } from './ResultTabs'
import { RunPanel, announceRun } from './RunStages'
import { readSources } from './sources'
import { DEFAULT_RUN_MODE, type RunMode } from './runMode'
import { SessionMenu } from './SessionRail'
import { StreamBanners } from './StreamBanners'
import { isEmptyChat, type RestoredTurn, type Turn } from './threadReducer'
import { useChatThread } from './useChatThread'

const EMPTY_GRAPH: GraphResponse = { nodes: [], edges: [] }

/** Where the memory rail's open/closed choice is remembered for this browser. */
const RAIL_PREF_KEY = 'aegis.console.memoryRail'

/**
 * The console's reading measure — the header, the thread and the composer share it.
 *
 * With the chat list moved out of the grid there is no column pushing the thread right
 * any more, and a conversation set edge to edge across 1920px is not a conversation
 * anybody reads. `64rem` is the width at which a turn still clears the `@[46rem]/turn`
 * query that gives the run panel its two columns, so the fan-out keeps its side-by-side
 * layout at every desktop width while the text stays a measure.
 */
const MEASURE = 'mx-auto w-full min-w-0 max-w-[64rem]'


/**
 * A question, as the person wrote it — set as the turn's own heading.
 *
 * Not a chat bubble. A bubble is right when two peers are talking; here one side is a
 * question and the other is a governed run with its own agents, sources and bill, and
 * setting the question as the heading of that block is what makes the answer read as
 * *its* answer rather than as the next message.
 */
function Question({ text, meta }: { text: string; meta: string }): ReactElement {
  return (
    <div className="flex flex-col gap-1">
      <h2 className="font-display text-lg leading-snug font-semibold tracking-tight text-balance text-foreground @[40rem]/turn:text-xl">
        {text}
      </h2>
      <span className="font-mono text-[0.68rem] text-muted-foreground">{meta}</span>
    </div>
  )
}

/**
 * One turn read back from the stored transcript.
 *
 * It renders what was said and nothing more: the event log that produced it is not
 * stored (`run_events` is backlog), so there are no agent cards and no tabs to open, and
 * saying so is more useful than an empty trace.
 */
function RestoredTurnView({ turn }: { turn: RestoredTurn }): ReactElement {
  return (
    <article className="flex flex-col gap-3 border-b border-border pb-6 last:border-b-0">
      {turn.question !== '' && <Question text={turn.question} meta="Sent earlier" />}
      {turn.answer !== '' && (
        <div className="rounded-lg border border-border bg-card px-4 py-3">
          <Badge tone="neutral" className="mb-2">
            from the transcript
          </Badge>
          <p className="text-[0.9rem] leading-relaxed whitespace-pre-wrap text-foreground">
            {turn.answer}
          </p>
        </div>
      )}
    </article>
  )
}

/**
 * The vision rails' verdict on this turn's image, before the answer.
 *
 * It sits above everything the run produced because that is the order the rails ran in:
 * the image was screened before the question was sent, and a refusal means the model
 * never saw the description. Showing the verdict afterwards would tell the story
 * backwards.
 */
function AttachmentChip({ attachment }: { attachment: TurnAttachment }): ReactElement {
  const verdict = attachmentVerdict(attachment)
  return (
    <div
      className={cn(
        'flex items-start gap-2 self-start rounded-lg border px-3 py-2 text-[0.76rem] leading-snug',
        verdict.blocked
          ? 'border-block/50 bg-block/10 text-block-ink'
          : 'border-border bg-surface-2/50 text-muted-foreground',
      )}
    >
      {verdict.blocked ? (
        <ShieldAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      ) : (
        <ShieldCheck aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      )}
      {/* eslint-disable-next-line @next/next/no-img-element -- a local data: URL, never a remote asset */}
      <img
        src={attachment.previewUrl}
        alt={attachment.filename ?? 'The attached image'}
        className="size-8 shrink-0 rounded-md border border-border object-cover"
      />
      <span className="max-w-[28rem]">
        <span className="font-medium">{verdict.label}.</span>{' '}
        {/* The rail's own reason leads, then the coverage — a refusal that lists only
            which controls ran tells the reader nothing they can act on. */}
        {verdict.reason !== '' ? `${verdict.reason} ` : ''}
        {verdict.detail}
      </span>
    </div>
  )
}

interface TurnViewProps {
  turn: Turn
  graph: GraphResponse
  metrics: MetricsResponse | null
  /** Open the skills drawer with a draft written from this turn. */
  onSaveAsSkill: (turn: Turn) => void
}

/**
 * One live turn: the question, the run that answered it, and the answer beneath.
 *
 * ## Four panels became one
 *
 * The order is still the order it happens in — the question rises to the top of the
 * block, the run forms under it, the answer forms under that, live rather than once the
 * run settles. What changed is how many things are doing that at once.
 *
 * A streaming turn used to animate four independent hierarchies side by side: a stage
 * timeline with its own head figures and fourteen always-open rows, a trust bar of four
 * lit chips, the lane board, and the activity rail. Fourteen simultaneous regions, no
 * focal point, and the owner's verdict was "too cluttered". They are now one
 * {@link RunPanel}: its header carries the run's figures and the four trust checks, its
 * left zone is the lane board — the fan-out is the money shot and it got *more* room out
 * of the merge — and its right zone is one feed of what is happening now, with the full
 * timeline a disclosure away.
 *
 * The lanes stay after the run finishes, dimmed and carrying their duration and cost:
 * who did what is part of the answer.
 */
function TurnView({ turn, graph, metrics, onSaveAsSkill }: TurnViewProps): ReactElement {
  const { run } = turn
  const beat = beatFromSignal(run.lastSignal)
  const running = run.running
  const reduced = useReducedMotion() ?? false
  const [tab, setTab] = useState<ResultTabId>('sources')
  // Once the run has stopped and anything at all arrived, the secondary tabs open. Each
  // one says what it is missing, which is more useful than withholding both.
  const settled = !running && run.events.length > 0

  const sent = new Date(turn.askedAt).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })

  return (
    /* The width this turn *asked* for, made available to the decision strip's width
       cell. `AnswerBlock` mounts that strip and is handed a run and nothing about the
       request behind it, and stating only the width that ran is exactly the silent
       override this console has been wrong about once already. */
    <RequestedWidth.Provider value={turn.mode}>
      <motion.article
        initial={reduced ? false : { opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: reduced ? 0 : 0.24, ease: [0.16, 1, 0.3, 1] }}
        data-turn=""
        className="@container/turn flex scroll-mt-4 min-w-0 flex-col gap-4 border-b border-border pb-8 last:border-b-0"
      >
        <Question
          text={turn.question}
          meta={running ? `Sent ${sent} · running` : `Sent ${sent}`}
        />

        {turn.attachment !== null && <AttachmentChip attachment={turn.attachment} />}

        <StreamBanners state={run} />

        {run.error !== null && (
          <p className="rounded-lg border border-block/50 bg-block/10 px-4 py-3 text-sm text-block-ink">
            <span className="font-semibold">The run stopped.</span> {run.error}
          </p>
        )}

        {/* Everything the run is doing, in one panel: its figures and trust checks in
            the header, its lanes on the left, and one feed of what is happening now on
            the right. Directly under the question, because the input rail takes three
            seconds before a single agent exists and this is what fills them. */}
        <RunPanel state={run}>
          <LaneBoard state={run} />
        </RunPanel>

        <AnswerBlock state={run} onSeeSources={() => setTab('sources')} />

        {settled && (
          <ResultTabs
            state={run}
            graph={graph}
            metrics={metrics}
            beat={beat}
            tab={tab}
            onTab={setTab}
            actions={
              // Only on a turn that produced an answer: a skill drafted from a run with
              // nothing in it would be a template with a question glued to the top, and
              // the button would be an invitation to save nothing.
              run.answer.trim() === '' ? null : (
                <button
                  type="button"
                  onClick={() => onSaveAsSkill(turn)}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface/70 px-2.5 py-1.5 text-[0.78rem] font-medium text-blue-700 outline-none transition-colors duration-[var(--dur-fast)] hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <BookOpen aria-hidden className="size-3.5" />
                  Save as skill
                </button>
              )
            }
          />
        )}
      </motion.article>
    </RequestedWidth.Provider>
  )
}

/**
 * The idle console — one field, the seed questions, and the path the answer will take.
 *
 * ## The complaint this rebuild answers
 *
 * The owner's verdict on the shipped console was *"going so far down, full memory being
 * seen"*. Both halves were this block's fault, and neither was about this block's own
 * content.
 *
 * The console was a three-column **grid** whose row height is the tallest column, and
 * the tallest column was the memory rail rendering every fact it held as its own
 * expanded card. That made the grid row 2,000px on a subject with a dozen facts. This
 * block, sitting in the middle column with `justify-center`, then centred itself against
 * that 2,000px — so an idle console with nothing on it put its own composer a thousand
 * pixels down, under a screen of white, beside a rail that ran off the bottom of the
 * document. The "vast empty void" and the "going so far down" were one bug wearing two
 * faces.
 *
 * The fix is structural and lives in {@link ChatConsole}: the console is a **fixed-height
 * frame** now, each column scrolls inside it, and the document does not scroll at all at
 * `lg` and up. Centring is correct again because it centres against the viewport rather
 * than against whatever the rail happened to fetch.
 *
 * ## What is on screen, and why each thing survived
 *
 * - **The composer**, at hero size, the only thing with a shadow and the only thing with
 *   a filled button. It is the screen's whole job.
 * - **The lockup and one line**, because a console with no thread needs to say what it
 *   is.
 * - **The adapter's own seed questions** — real configuration, not fabricated examples,
 *   and the fastest way for someone watching to see a real run.
 * - **{@link RunPreview}** — the four beats and twelve named rails a question passes
 *   through. It carries no figures at all, because nothing has been measured yet, and it
 *   is the same spine {@link RunStages} fills in the moment a question is sent.
 *
 * Everything else — memory, activity, stages, lanes, trace — is absent until there is a
 * run to talk about, or one click away behind the header. No placeholder cards, no
 * sample results, no invented domain copy.
 */
function IdleConsole({
  children,
  samples,
  onPick,
}: {
  children: ReactElement
  samples: string[]
  onPick: (question: string) => void
}): ReactElement {
  const reduced = useReducedMotion() ?? false
  return (
    <motion.div
      initial={reduced ? false : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduced ? 0 : 0.28, ease: [0.16, 1, 0.3, 1] }}
      className="flex min-w-0 flex-1 flex-col items-center justify-center gap-5 px-2 py-6"
    >
      <div className="flex w-full max-w-2xl flex-col items-center gap-6">
        <div className="flex flex-col items-center gap-1.5 text-center">
          <AegisLockup size="lg" />
          <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
            Ask one question. Every step it takes is named, sourced and priced as it
            happens.
          </p>
        </div>

        <div className="w-full">{children}</div>

        {/* The seed chips sit under the composer's own left edge rather than centred.
            Three questions of three different lengths, centred, read as a ragged stack;
            aligned to the box they read as suggestions for it. */}
        {samples.length > 0 && (
          <div className="flex w-full min-w-0 flex-wrap items-center gap-1.5">
            <span className="eyebrow mr-1">Try</span>
            {samples.map((sample) => (
              <button
                key={sample}
                type="button"
                onClick={() => onPick(sample)}
                className="max-w-full min-w-0 truncate rounded-full border border-border bg-surface/60 px-3 py-1 text-left font-mono text-[0.7rem] text-muted-foreground outline-none transition-colors hover:border-blue-200 hover:text-blue-700 focus-visible:ring-2 focus-visible:ring-ring"
                title={sample}
              >
                {sample}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* The one thing an empty console can say that is true. Held to the composer's
          own width so the two read as one block rather than as a page of panels. */}
      <div className="mt-2 w-full max-w-2xl">
        <RunPreview />
      </div>
    </motion.div>
  )
}

/**
 * The memory toggle — a chip that states what the agent holds, and opens the rail.
 *
 * ## Why the rail is closed by default now
 *
 * It was open, and it was the tallest thing on an idle console. The rail is *context*:
 * it is what the agent knows about you, which is worth a glance and is never the reason
 * anybody opened this screen. A screen whose one job is to take a question should not
 * spend a third of its width and all of its height on a list of facts nobody asked to
 * read.
 *
 * So the rail opens on a click, and the chip is what makes that acceptable: it carries
 * the count, so the record is *stated* even while it is closed, and while a run is in
 * flight it carries what that run actually pulled out of memory instead. The choice is
 * remembered for the browser, so somebody who wants it open never has to open it twice.
 *
 * Both figures are the wire's — `is_valid` rows from `GET /memory/facts`, and
 * `recalled_fact_count` from the run's own `memory` event. When neither has answered
 * yet the chip states no number at all rather than a zero.
 */
function MemoryChip({
  open,
  count,
  recalled,
  onToggle,
}: {
  open: boolean
  /** Current facts the store holds, or null while the read is in flight or refused. */
  count: number | null
  /** Facts the newest run recalled, or null when memory did not run. */
  recalled: number | null
  onToggle: () => void
}): ReactElement {
  const figure = recalled ?? count
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      data-memory-toggle=""
      title={
        recalled !== null
          ? 'What this run recalled, and everything the agent has kept about you'
          : 'What the agent has kept about you'
      }
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[0.78rem] font-medium outline-none transition-colors duration-[var(--dur-fast)] focus-visible:ring-2 focus-visible:ring-ring',
        open
          ? 'border-blue-200 bg-blue-50 text-blue-700'
          : 'border-border bg-surface/70 text-muted-foreground hover:border-blue-200 hover:text-blue-700',
      )}
    >
      <Brain aria-hidden className="size-3.5" />
      {recalled !== null ? 'Recalled' : 'What I know'}
      {figure !== null && (
        <span className="tabular font-mono text-[0.72rem]">{figure}</span>
      )}
    </button>
  )
}

/**
 * The console's own way into skills — the same drawer the turn's action opens.
 *
 * It sits in the console header rather than only on a finished turn because the two
 * reasons to write a skill are different moments: one is "that run went well, keep it",
 * and the other is "our refund window is 30 days and the agent should know". The second
 * one has no run behind it and used to require leaving for the settings screen.
 *
 * It says **Add a skill** rather than "Skills". The owner's verdict on the shipped
 * console was that there was *no way to add a skill in the UI* — while this button, and
 * the editor behind it, had been here the whole time. A noun labels a place; somebody
 * looking for an action scans past it. The drawer still lists what is in force the
 * moment it opens, so nothing is lost by naming the thing people are hunting for.
 */
function SkillsButton({ onOpen }: { onOpen: () => void }): ReactElement {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-[0.78rem] font-medium text-blue-700 outline-none transition-colors duration-[var(--dur-fast)] hover:bg-blue-100 focus-visible:ring-2 focus-visible:ring-ring"
    >
      <BookOpen aria-hidden className="size-3.5" />
      Add a skill
    </button>
  )
}

/** The two ways to watch a turn: as a conversation, or as the graph it walks. */
type ConsoleView = 'run' | 'flow'

/** The Run / Flow switch — the main view's own tabs, above the thread. */
function ViewTabs({
  view,
  onView,
}: {
  view: ConsoleView
  onView: (view: ConsoleView) => void
}): ReactElement {
  const tabs: { id: ConsoleView; label: string }[] = [
    { id: 'run', label: 'Run' },
    { id: 'flow', label: 'Flow' },
  ]
  return (
    <div
      role="tablist"
      aria-label="Console view"
      className="inline-flex items-center gap-0.5 rounded-full border border-border bg-surface-2/60 p-0.5"
    >
      {tabs.map((tab) => {
        const selected = view === tab.id
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`console-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`console-panel-${tab.id}`}
            // WAI roving tabindex: one tab stop for the whole tablist, arrows move
            // between tabs. Without this each tab is its own stop, so a keyboard user
            // walks through every one to get past the control.
            tabIndex={selected ? 0 : -1}
            onKeyDown={(event) => {
              const order = tabs.map((t) => t.id)
              const at = order.indexOf(tab.id)
              const to =
                event.key === 'ArrowRight' ? order[(at + 1) % order.length]
                : event.key === 'ArrowLeft' ? order[(at - 1 + order.length) % order.length]
                : event.key === 'Home' ? order[0]
                : event.key === 'End' ? order[order.length - 1]
                : null
              if (to == null) return
              event.preventDefault()
              onView(to)
              document.getElementById(`console-tab-${to}`)?.focus()
            }}
            onClick={() => onView(tab.id)}
            className={cn(
              'rounded-full px-3.5 py-1 text-[0.8rem] font-medium transition-colors duration-[var(--dur-fast)]',
              'outline-none focus-visible:ring-2 focus-visible:ring-ring',
              selected
                ? 'bg-card text-foreground shadow-card'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

/**
 * The chat console — session rail, thread, composer.
 *
 * The shape is a conversation, not a dashboard: idle is the wordmark and one field, and
 * a sent question rises to the top of its own block with the run forming underneath it.
 * Every panel the old three-column bento owned survives, re-homed rather than rewritten
 * — the decision strip and answer under the question, the rerank scoreboard in Sources,
 * the guardrail glass box, node timeline, trace log, graph and efficiency panel in
 * Trace, the trust bar, stream banners and approval spotlight inside the live turn, and
 * the orchestration graph promoted to its own Flow tab.
 */
export function ChatConsole({ role }: { role: Role }): ReactElement {
  const { session: auth, hydrated } = useAuth()
  const token = auth?.token ?? null

  const chat = useChatThread(token)
  const metrics = useMetrics(token)

  const [graph, setGraph] = useState<GraphResponse>(EMPTY_GRAPH)
  const [personaId, setPersonaId] = useState(personasForRole(role)[0]?.id ?? '')
  const [mode, setMode] = useState<RunMode>(DEFAULT_RUN_MODE)
  const [view, setView] = useState<ConsoleView>('run')
  const [decided, setDecided] = useState(false)
  // The skills drawer, and the draft it opens with. `null` is a skill written from the
  // blank template; a string is one drafted from a finished run.
  const [skillsOpen, setSkillsOpen] = useState(false)
  const [skillDraft, setSkillDraft] = useState<string | null>(null)
  // Bumped every time a run settles. The budget line re-reads on it rather than on a
  // timer, because a settled run is the only thing that can have moved the figure.
  const [budgetKey, setBudgetKey] = useState(0)
  // Whether the memory rail is on screen. Closed by default — see {@link MemoryChip} —
  // and remembered for the browser, so opening it is a decision somebody makes once.
  const [railOpen, setRailOpen] = useState(false)
  const threadEndRef = useRef<HTMLDivElement>(null)
  const threadRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setRailOpen(window.localStorage.getItem(RAIL_PREF_KEY) === 'open')
  }, [])

  const rememberRail = (open: boolean): void => {
    try {
      window.localStorage.setItem(RAIL_PREF_KEY, open ? 'open' : 'closed')
    } catch {
      // A browser with storage denied still gets the toggle; it just forgets it.
    }
  }

  const toggleRail = useCallback((): void => {
    setRailOpen((was) => {
      rememberRail(!was)
      return !was
    })
  }, [])

  const closeRail = useCallback((): void => {
    rememberRail(false)
    setRailOpen(false)
  }, [])

  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !chat.running) setBudgetKey((n) => n + 1)
    wasRunning.current = chat.running
  }, [chat.running])

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    void getGraph(token)
      .then((g) => {
        if (alive) setGraph(g)
      })
      // Fall back to the honest empty graph so the panel renders its empty state
      // rather than waiting on data that will never arrive.
      .catch(() => {
        if (alive) setGraph(EMPTY_GRAPH)
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  const current = chat.session
  const turnCount = (current?.turns.length ?? 0) + (current?.restored.length ?? 0)
  const idle = isEmptyChat(current)

  // Follow the newest turn as it arrives — and again when it settles.
  //
  // Once was not enough. The secondary tabs only mount when the run stops, so the scroll
  // that ran the moment the question was sent left them below the fold, under a sticky
  // composer. `chat.running` flipping false is exactly the moment they exist, so that is
  // the second time to look.
  //
  // Clearance is `scroll-mb-48` on the end marker rather than padding on the thread:
  // `scrollIntoView` honours scroll-margin, so the newest content lands *above* the
  // composer instead of behind it, and nothing has to guess the composer's height.
  //
  // While the run is live the target is the *top* of the newest turn, not the bottom of
  // the thread. A live turn is a tall block — the stage spine, the lanes, the activity
  // rail — and scrolling to its end parked the viewport on an empty answer card with
  // everything that was actually happening above the fold. The complaint that a
  // seventy-second run "shows nothing" was partly this: it was showing plenty, off
  // screen. Once the run settles the end is the right target again, because by then the
  // answer and its tabs are the thing to be looking at.
  useEffect(() => {
    if (view !== 'run') return
    const turns = threadRef.current?.querySelectorAll('[data-turn]')
    const newest = turns?.[turns.length - 1]
    if (newest === undefined) {
      threadEndRef.current?.scrollIntoView({ block: 'end' })
      return
    }
    if (chat.running) {
      newest.scrollIntoView({ block: 'start' })
      return
    }
    // Settled: the answer, not the end of the turn. Scrolling to the end landed the
    // reader on the tail of "Every ranked source", several screens below the thing they
    // asked for. The answer block marks itself with `data-answer` for exactly this.
    const answer = newest.querySelector('[data-answer]')
    if (answer !== null) answer.scrollIntoView({ block: 'start' })
    else newest.scrollIntoView({ block: 'start' })
  }, [turnCount, chat.running, view])

  const send = (question: string, attachment: TurnAttachment | null = null): void => {
    setDecided(false)
    setView('run')
    chat.ask({ question, persona: personaId, attachment, mode })
  }

  /**
   * Draft a skill from a finished turn.
   *
   * Everything in the draft is read off the run — the question as it was written, the
   * tools it actually called, the sources it actually stood on. The console never
   * writes a step the run did not take; the author edits what happened into what should
   * happen next time. See {@link draftFromRun}.
   */
  const saveAsSkill = (turn: Turn): void => {
    setSkillDraft(
      draftFromRun({
        question: turn.question,
        answer: turn.run.answer,
        tools: turn.run.toolCalls.map((call) => call.tool),
        sources: readSources(turn.run.retrievalScores).map((source) => source.label),
      }),
    )
    setSkillsOpen(true)
  }

  const openSkills = (): void => {
    setSkillDraft(null)
    setSkillsOpen(true)
  }

  const handleDecision = (decision: ApprovalDecision): void => {
    setDecided(true)
    chat.resolveApproval(decision)
  }

  const persona = getPersona(personaId)
  const samples = persona?.sampleQueries ?? []
  const approval = chat.live?.run.approval ?? null
  // The subject `/memory/*` is keyed on — read from the bearer's own claim, so the rail
  // asks for the one record this sign-in is allowed to see.
  const memorySubject = memorySubjectOf(auth)

  // The rail's read of the durable facts, owned here rather than inside the rail.
  //
  // The header chip states the count while the rail is **closed**, so the read has to
  // outlive the rail's mount. It is still one request answering one question: it is the
  // digest's content, the probe that decides whether any memory card is offered, and
  // the chip's figure.
  const facts = useAsync<MemoryFactsResponse>(
    () =>
      memorySubject === null
        ? Promise.reject(new Error('This sign-in owns no memory subject.'))
        : getMemoryFacts(token, memorySubject, true),
    [token, memorySubject],
  )
  const factCount =
    facts.state.status === 'ready' ? digestFacts(facts.state.data.rows).current : null
  // The chip follows the rail's own rule: a reading that is refused, or a bearer with no
  // memory subject at all, withdraws the offer rather than opening a panel to say so. A
  // 500 or a dropped connection does not — that reading is absent right now, not absent
  // for this person. The demo `client` sign-in is the refused case, and it used to get a
  // chip whose only content was an apology.
  const memoryOffered = memoryAvailable(memoryAccessOf(memorySubject, facts.state))

  // The Flow tab draws the newest run — the live one while a turn streams, the last one
  // once it has settled. Never an invented empty graph state: `FlowCanvas` handles a
  // run that has not started by drawing the topology with nothing lit.
  const newest: RunState | null = chat.live?.run ?? current?.turns.at(-1)?.run ?? null
  // What the newest run pulled out of memory. Null means recall did not run — every
  // turn without a `session_id` — and the chip says nothing rather than zero.
  const turnRecall = newest?.memory ?? null

  // Between `lg` and `xl` the rail is an overlay, and an overlay that covers the Send
  // button has to be dismissible the way every other overlay on this screen is. Above
  // `xl` it is a real column and Escape closing a column would be a surprise, so the
  // listeners are attached only in the band where it actually floats.
  const railRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!(railOpen && view !== 'flow')) return
    if (!window.matchMedia('(min-width: 64rem) and (max-width: 79.98rem)').matches) return
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') closeRail()
    }
    const onDown = (event: MouseEvent): void => {
      if (!(event.target instanceof Element)) return
      if (railRef.current?.contains(event.target) === true) return
      // The chip owns the toggle; letting this fire on it would close and reopen in one
      // click and the rail would look stuck.
      if (event.target.closest('[data-memory-toggle]') !== null) return
      closeRail()
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
    }
  }, [railOpen, view, closeRail])

  const composer = (
    <Composer
      role={role}
      personaId={personaId}
      onPersonaChange={setPersonaId}
      mode={mode}
      onModeChange={setMode}
      variant={idle ? 'hero' : 'docked'}
      token={token}
      budgetKey={budgetKey}
      onSend={send}
      running={chat.running}
    />
  )

  const railShown = railOpen && view !== 'flow' && memoryOffered

  return (
    /*
     * The console is a **frame**, not a document.
     *
     * It used to be `grid min-h-[70vh]` in normal document flow, and a CSS grid row is
     * as tall as its tallest column. The tallest column was the memory rail, rendering
     * every fact the store held as its own expanded card — so a subject with a dozen
     * facts made the *row* two thousand pixels tall, the idle console scrolled for
     * several viewports with no run on it, and the composer, centred inside that row,
     * sat a thousand pixels down beneath a screen of white. One structure, both of the
     * owner's complaints.
     *
     * From `lg` up the console is therefore given exactly the height it has — the
     * viewport, less the topbar and the shell's own padding — and each column scrolls
     * inside it. The document does not scroll at all. That is categorical: no number of
     * facts, chat sessions, stage rows or answer paragraphs can grow the page again,
     * because the page is not what grows. Below `lg` there are no columns to contain and
     * it stays in document flow, which is the right behaviour on a phone.
     *
     * `8rem` is `h-16` of sticky topbar plus `py-8` twice, from the portal layout.
     *
     * **The chat list is no longer one of those columns.** It was a permanent `13rem`
     * track, so the thread — the thing the console is *for* — was off-centre at every
     * desktop width, pushed right by a list a person opens perhaps twice a session. It
     * lives behind one header control now (see {@link SessionMenu}), and the thread is
     * the only column, held to a reading measure and centred in whatever is left. The
     * frame itself is unchanged: still `dvh`, still per-column scrollers, still a
     * document that does not scroll.
     */
    <div
      className={cn(
        'relative flex flex-col gap-4',
        'lg:grid lg:h-[calc(100dvh-8rem)] lg:grid-cols-[minmax(0,1fr)] lg:overflow-hidden',
        railShown && 'xl:grid-cols-[minmax(0,1fr)_20rem]',
      )}
    >
      <div className="flex min-w-0 flex-col gap-3 lg:min-h-0 lg:overflow-hidden">
        {/* One header row, in both states, so the console has a chrome that does not
            move when the first question is sent. Idle has nothing to switch between,
            so the tabs appear with the thread rather than sitting there disabled.

            Held to the same measure as the thread beneath it, so the chat control and
            the composer share one left edge instead of floating apart at 1920. */}
        <div className={cn(MEASURE, 'flex shrink-0 flex-wrap items-center gap-2')}>
          <SessionMenu
            sessions={chat.thread.sessions}
            activeId={chat.thread.activeSessionId}
            onSelect={chat.selectChat}
            onNew={chat.newChat}
          />
          {!idle && <ViewTabs view={view} onView={setView} />}
          {!idle && view === 'flow' && (
            <span className="flex items-center gap-1.5 text-[0.72rem] text-muted-foreground">
              <Workflow aria-hidden className="size-3.5" />
              The compiled graph, read from the running backend
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            {view !== 'flow' && memoryOffered && (
              <MemoryChip
                open={railOpen}
                count={factCount}
                recalled={turnRecall?.recalled_fact_count ?? null}
                onToggle={toggleRail}
              />
            )}
            <SkillsButton onOpen={openSkills} />
          </div>
        </div>

        {/*
          The console's status line, for a screen reader.

          Until this existed the console had exactly one live region — the answer panel —
          so a run starting, a run finishing and a lane failing were all silent: somebody
          not watching the lanes animate had no way to know a seventy-second run had
          begun, or that it had stopped. It lives out here rather than inside the turn
          because a live region has to be in the document *before* its text changes to be
          announced, and a turn mounts at the same instant its run starts.

          `announceRun` is a pure function of the newest run, so the text changes once per
          transition rather than on every token.
        */}
        <p role="status" aria-live="polite" className="sr-only">
          {announceRun(newest)}
        </p>

        {idle ? (
          <div className="flex min-h-0 flex-1 flex-col lg:overflow-y-auto">
            <IdleConsole samples={samples} onPick={send}>
              {composer}
            </IdleConsole>
          </div>
        ) : (
          <>
            {/* The thread scrolls **inside** the frame. That also fixes the turn
                heading: `scrollIntoView` used to park the question under the sticky
                topbar, because the scroller was the document and the topbar was over
                it. Here the scroller is this box and its top edge is visible. */}
            <div
              ref={threadRef}
              role="tabpanel"
              id="console-panel-run"
              aria-labelledby="console-tab-run"
              hidden={view !== 'run'}
              className="flex min-h-0 flex-1 flex-col lg:overflow-y-auto lg:pt-0.5 lg:pr-1"
            >
              {/* The scroller stays full width so its scrollbar sits at the frame's own
                  edge; the measure is on the content inside it, so the thread is centred
                  rather than stretched across 1920px of window. */}
              <div className={cn(MEASURE, 'flex min-w-0 flex-col gap-6')}>
                {current?.restored.map((turn) => (
                  <RestoredTurnView key={`restored-${turn.turnIndex}`} turn={turn} />
                ))}
                {current?.turns.map((turn) => (
                  <TurnView
                    key={turn.id}
                    turn={turn}
                    graph={graph}
                    metrics={metrics}
                    onSaveAsSkill={saveAsSkill}
                  />
                ))}
                <div ref={threadEndRef} className="scroll-mb-8" />
              </div>
            </div>

            {/* Mounted only while it is the selected view, and that is load-bearing
                rather than tidy: React Flow measures its container once on mount and
                fits the graph to what it measured. A panel hidden with `hidden`
                measures 0×0, so a flow-map mounted behind the Run tab fitted itself to
                nothing and opened scrolled to the tail of the graph. */}
            {view === 'flow' && (
              <div
                role="tabpanel"
                id="console-panel-flow"
                aria-labelledby="console-tab-flow"
                className="min-h-0 flex-1"
              >
                {newest === null ? (
                  <p className="rounded-lg border border-border bg-surface-2/40 px-4 py-6 text-sm text-muted-foreground">
                    This chat has a stored transcript but no run in this tab, so there is
                    no trajectory to draw. Ask a question to watch the graph execute.
                  </p>
                ) : (
                  <FlowCanvas state={newest} height={460} />
                )}
              </div>
            )}

            {/* The composer is the frame's footer now, not a sticky bar floating over
                the thread on a translucent blur. DESIGN.md §1 rules out backdrop-blur
                on content, and the blur was also what let the assistant's face sit on
                top of the activity log while a run streamed. */}
            <div className="shrink-0 border-t border-border bg-background pt-2 pb-1">
              <div className={MEASURE}>
                <div className="px-1 pb-1">
                  <AssistantBot running={chat.running} />
                </div>
                {composer}
              </div>
            </div>
          </>
        )}
      </div>

      {/*
        What the agent has learned — context beside the conversation, never the
        conversation itself.

        Three widths, three behaviours. Below `lg` it stacks under the thread in
        document flow. At `lg` there is no room for a third column without squeezing
        the thread to nothing, so it is an overlay panel on the right of the frame.
        From `xl` it is a real grid column. The Flow tab takes the width back either
        way: a fourteen-layer graph in two thirds of the page is a graph nobody can
        read.
      */}
      {railShown && (
        <div
          ref={railRef}
          className={cn(
            'min-w-0 xl:overflow-y-auto',
            // `lg:max-xl:` rather than `lg:` overridden by `xl:` — one range, one set of
            // rules, so nothing depends on which utility the stylesheet emits last.
            'lg:max-xl:absolute lg:max-xl:inset-y-0 lg:max-xl:right-0 lg:max-xl:z-20',
            'lg:max-xl:w-[20rem] lg:max-xl:overflow-y-auto lg:max-xl:rounded-lg',
            'lg:max-xl:border lg:max-xl:border-border lg:max-xl:bg-card lg:max-xl:p-3 lg:max-xl:shadow-pop',
          )}
        >
          <MemoryRail
            token={token}
            subject={memorySubject}
            facts={facts.state}
            turnRecall={turnRecall}
            onClose={closeRail}
          />
        </div>
      )}

      {/* Skills, over the console rather than a screen away from it. */}
      <SkillsDrawer
        open={skillsOpen}
        onClose={() => setSkillsOpen(false)}
        token={token}
        draft={skillDraft}
      />

      {/* Human-approval spotlight — scrims the console while a decision is due. */}
      {approval !== null && (
        <ApprovalSpotlight>
          <ApprovalCard
            approval={approval}
            onDecision={handleDecision}
            resolved={decided || chat.approvalResolved}
          />
        </ApprovalSpotlight>
      )}
    </div>
  )
}
