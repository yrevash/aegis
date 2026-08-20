'use client'

import { ShieldAlert, ShieldCheck, Workflow } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState, type ReactElement } from 'react'

import { ApprovalCard } from '@/components/approval/ApprovalCard'
import { AegisLockup } from '@/components/brand/AegisLockup'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/primitives/button'
import { TrustBar } from '@/components/layout/TrustBar'
import { getGraph } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import { getPersona, personasForRole } from '@/config/personas'
import { useMetrics } from '@/state/useMetrics'
import type { ApprovalDecision, GraphResponse, MetricsResponse } from '@/lib/api/types'
import type { Role } from '@/lib/stream'
import type { RunState } from '@/state/runReducer'

import { ActivityRail } from './ActivityRail'
import { LaneBoard } from './LaneBoard'
import { deriveActivity } from './agentLanes'
import { ApprovalSpotlight } from './ApprovalSpotlight'
import { AssistantBot } from './AssistantBot'
import { Composer } from './Composer'
import { attachmentVerdict, type TurnAttachment } from './composerAttachment'
import { FlowCanvas } from './FlowCanvas'
import { MemoryRail } from './MemoryRail'
import { memorySubjectOf } from './memorySubject'
import { beatFromSignal } from './motion'
import { AnswerBlock, ResultTabs, type ResultTabId } from './ResultTabs'
import { DEFAULT_RUN_MODE, type RunMode } from './runMode'
import { SessionRail } from './SessionRail'
import { StreamBanners } from './StreamBanners'
import { isEmptyChat, type RestoredTurn, type Turn } from './threadReducer'
import { useChatThread } from './useChatThread'

const EMPTY_GRAPH: GraphResponse = { nodes: [], edges: [] }

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
      <h2 className="font-display text-lg leading-snug font-semibold tracking-tight text-balance text-foreground sm:text-xl">
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
        <span className="font-medium">{verdict.label}.</span> {verdict.detail}
      </span>
    </div>
  )
}

interface TurnViewProps {
  turn: Turn
  graph: GraphResponse
  metrics: MetricsResponse | null
}

/**
 * One live turn: the question, the lanes that answered it, and the answer beneath them.
 *
 * The order is the order it happens in. The question rises to the top of the block, the
 * agent lanes appear and stream their own thinking inside their own cards, and the
 * answer forms underneath — live, not once the run settles. The activity rail sits
 * beside the lanes while the run is in flight because it is the *watching*; when the run
 * stops it folds into a summary line, because it was never the result.
 *
 * The cards stay after the run finishes, dimmed and carrying their duration and cost:
 * who did what is part of the answer.
 */
function TurnView({ turn, graph, metrics }: TurnViewProps): ReactElement {
  const { run } = turn
  const beat = beatFromSignal(run.lastSignal)
  const running = run.running
  const activityCount = deriveActivity(run).length
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
    <motion.article
      initial={reduced ? false : { opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduced ? 0 : 0.24, ease: [0.16, 1, 0.3, 1] }}
      className="@container/turn flex flex-col gap-4 border-b border-border pb-8 last:border-b-0"
    >
      <Question text={turn.question} meta={running ? `Sent ${sent} · running` : `Sent ${sent}`} />

      {turn.attachment !== null && <AttachmentChip attachment={turn.attachment} />}

      {running && <TrustBar state={run} beat={beat} idle={false} />}

      <StreamBanners state={run} />

      {run.error !== null && (
        <p className="rounded-lg border border-block/50 bg-block/10 px-4 py-3 text-sm text-block-ink">
          <span className="font-semibold">The run stopped.</span> {run.error}
        </p>
      )}

      {/* Again a container query: the activity rail only earns a column when the turn
          itself is wide, which at 1440px with both rails out it is not. */}
      <div
        className={cn(
          'grid gap-4',
          running && '@[46rem]/turn:grid-cols-[minmax(0,1fr)_15rem]',
        )}
      >
        <div className="flex min-w-0 flex-col gap-3">
          <LaneBoard state={run} />
        </div>

        {running ? (
          <div className="min-w-0">
            <ActivityRail state={run} />
          </div>
        ) : (
          activityCount > 0 && (
            <details className="rounded-lg border border-border bg-surface-2/30 px-3 py-2">
              <summary className="cursor-pointer text-[0.78rem] font-medium text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
                Activity · {activityCount}
              </summary>
              <div className="pt-2">
                <ActivityRail state={run} />
              </div>
            </details>
          )
        )}
      </div>

      <AnswerBlock state={run} onSeeSources={() => setTab('sources')} />

      {settled && (
        <ResultTabs
          state={run}
          graph={graph}
          metrics={metrics}
          beat={beat}
          tab={tab}
          onTab={setTab}
        />
      )}
    </motion.article>
  )
}

/**
 * The idle console — the wordmark, one field, and the four axes of a turn beneath it.
 *
 * No placeholder cards, no sample results, no invented domain copy: an empty console has
 * nothing measured to show, and drawing something would be the only untrue thing on the
 * screen. What it carries is the one action that fills it, and the adapter's own seed
 * questions, which are real configuration rather than a fabricated example.
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
      className="flex min-w-0 flex-1 flex-col items-center justify-center gap-6 px-2 py-10 sm:py-16"
    >
      <div className="flex flex-col items-center gap-2 text-center">
        <AegisLockup size="lg" />
        <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
          Ask one question. Every step it takes is named, sourced and priced as it happens.
        </p>
      </div>

      <div className="w-full max-w-3xl">{children}</div>

      {samples.length > 0 && (
        <div className="flex w-full max-w-3xl min-w-0 flex-wrap items-center justify-center gap-1.5">
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
    </motion.div>
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
  // Bumped every time a run settles. The budget line re-reads on it rather than on a
  // timer, because a settled run is the only thing that can have moved the figure.
  const [budgetKey, setBudgetKey] = useState(0)
  const threadEndRef = useRef<HTMLDivElement>(null)

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
  useEffect(() => {
    if (view !== 'run') return
    threadEndRef.current?.scrollIntoView({ block: 'end' })
  }, [turnCount, chat.running, view])

  const send = (question: string, attachment: TurnAttachment | null = null): void => {
    setDecided(false)
    setView('run')
    chat.ask({ question, persona: personaId, attachment, mode })
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
  // The Flow tab draws the newest run — the live one while a turn streams, the last one
  // once it has settled. Never an invented empty graph state: `FlowCanvas` handles a
  // run that has not started by drawing the topology with nothing lit.
  const newest: RunState | null = chat.live?.run ?? current?.turns.at(-1)?.run ?? null

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

  return (
    <div className="grid min-h-[70vh] gap-4 lg:grid-cols-[13rem_minmax(0,1fr)] xl:grid-cols-[13rem_minmax(0,1fr)_21rem]">
      {/* The rail is a sidebar from lg up, and a compact picker below it. */}
      <aside className="hidden lg:block">
        <SessionRail
          sessions={chat.thread.sessions}
          activeId={chat.thread.activeSessionId}
          onSelect={chat.selectChat}
          onNew={chat.newChat}
        />
      </aside>

      <div className={cn('flex min-w-0 flex-col gap-3', view === 'flow' && 'xl:col-span-2')}>
        <div className="flex items-center gap-2 lg:hidden">
          <label htmlFor="chat-picker" className="sr-only">
            Chat
          </label>
          <select
            id="chat-picker"
            value={chat.thread.activeSessionId}
            onChange={(event) => chat.selectChat(event.target.value)}
            className="h-9 min-w-0 flex-1 rounded-md border border-input bg-surface/60 px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            {chat.thread.sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title === '' ? 'New chat' : s.title}
              </option>
            ))}
          </select>
          <Button variant="outline" size="sm" onClick={chat.newChat}>
            New chat
          </Button>
        </div>

        {idle ? (
          <IdleConsole samples={samples} onPick={send}>
            {composer}
          </IdleConsole>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <ViewTabs view={view} onView={setView} />
              {view === 'flow' && (
                <span className="flex items-center gap-1.5 text-[0.72rem] text-muted-foreground">
                  <Workflow aria-hidden className="size-3.5" />
                  The compiled graph, read from the running backend
                </span>
              )}
            </div>

            <div
              role="tabpanel"
              id="console-panel-run"
              aria-labelledby="console-tab-run"
              hidden={view !== 'run'}
              className="flex min-h-0 flex-1 flex-col gap-6"
            >
              {current?.restored.map((turn) => (
                <RestoredTurnView key={`restored-${turn.turnIndex}`} turn={turn} />
              ))}
              {current?.turns.map((turn) => (
                <TurnView key={turn.id} turn={turn} graph={graph} metrics={metrics} />
              ))}
              <div ref={threadEndRef} className="scroll-mb-48" />
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

            <div className="sticky bottom-0 bg-background/95 pt-2 pb-1 backdrop-blur">
              <div className="px-1 pb-1">
                <AssistantBot running={chat.running} />
              </div>
              {composer}
            </div>
          </>
        )}
      </div>

      {/* What the agent has learned, beside the conversation that taught it. The Flow
          tab is the one view that wants the width back: a fourteen-layer graph in a
          third of the page is a graph nobody can read. */}
      {view !== 'flow' && (
        <div className="min-w-0 lg:col-span-2 xl:col-span-1">
          <MemoryRail token={token} subject={memorySubject} />
        </div>
      )}

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
