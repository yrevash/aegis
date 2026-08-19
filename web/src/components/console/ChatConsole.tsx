'use client'

import { Sparkles } from 'lucide-react'
import { useEffect, useRef, useState, type ReactElement } from 'react'

import { ApprovalCard } from '@/components/approval/ApprovalCard'
import { Badge } from '@/components/primitives/badge'
import { Button } from '@/components/primitives/button'
import { TrustBar } from '@/components/layout/TrustBar'
import { getGraph } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import { getPersona, personasForRole } from '@/config/personas'
import { useMetrics } from '@/state/useMetrics'
import type { ApprovalDecision, GraphResponse, MetricsResponse } from '@/lib/api/types'
import type { Role } from '@/lib/stream'

import { ActivityRail } from './ActivityRail'
import { AgentPanel } from './AgentPanel'
import { deriveActivity } from './agentLanes'
import { ApprovalSpotlight } from './ApprovalSpotlight'
import { AssistantBot } from './AssistantBot'
import { Composer } from './Composer'
import { MemoryRail } from './MemoryRail'
import { memorySubjectFromToken } from './memorySubject'
import { beatFromSignal } from './motion'
import { ReasoningLane } from './ReasoningLane'
import { ResultTabs } from './ResultTabs'
import { SessionRail } from './SessionRail'
import { StreamBanners } from './StreamBanners'
import { isEmptyChat, type RestoredTurn, type Turn } from './threadReducer'
import { useChatThread } from './useChatThread'

const EMPTY_GRAPH: GraphResponse = { nodes: [], edges: [] }

/** A question, as the person wrote it. */
function Question({ text, meta }: { text: string; meta: string }): ReactElement {
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="max-w-[46rem] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm leading-relaxed text-primary-foreground">
        {text}
      </div>
      <span className="font-mono text-[0.66rem] text-muted-foreground">{meta}</span>
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
    <article className="flex flex-col gap-3">
      {turn.question !== '' && <Question text={turn.question} meta="Sent earlier" />}
      {turn.answer !== '' && (
        <div className="rounded-2xl rounded-bl-md border border-border bg-card px-4 py-3">
          <Badge variant="outline" className="mb-2">
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

interface TurnViewProps {
  turn: Turn
  graph: GraphResponse
  metrics: MetricsResponse | null
}

/**
 * One live turn: the question, the run as it happens, and the result once it has.
 *
 * While the run streams, the agent panel and the activity rail sit side by side — the
 * lanes that have an owner and the events that do not. When it finishes, the cards stay
 * (dimmed, with their duration and cost) because who did what is part of the answer,
 * and the activity rail folds away because it was the *watching*, not the result.
 */
function TurnView({ turn, graph, metrics }: TurnViewProps): ReactElement {
  const { run } = turn
  const beat = beatFromSignal(run.lastSignal)
  const running = run.running
  const activityCount = deriveActivity(run).length
  // Once the run has stopped and anything at all arrived, the tabs open. Each tab says
  // what it is missing, which is more useful than withholding all three.
  const settled = !running && run.events.length > 0

  const sent = new Date(turn.askedAt).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })

  return (
    <article className="flex flex-col gap-3">
      <Question text={turn.question} meta={running ? `Sent ${sent} · running` : `Sent ${sent}`} />

      {running && <TrustBar state={run} beat={beat} idle={false} />}

      <StreamBanners state={run} />

      {run.error !== null && (
        <p className="rounded-lg border border-block/50 bg-block/10 px-4 py-3 text-sm text-block-ink">
          <span className="font-semibold">The run stopped.</span> {run.error}
        </p>
      )}

      <div className={cn('grid gap-4', running && 'lg:grid-cols-[1fr_18rem]')}>
        <div className="flex min-w-0 flex-col gap-3">
          <AgentPanel state={run} />
          <ReasoningLane state={run} />
        </div>

        {running ? (
          <div className="min-w-0">
            <ActivityRail state={run} />
          </div>
        ) : (
          activityCount > 0 && (
            <details className="rounded-lg border border-border bg-surface-2/30 px-3 py-2">
              <summary className="cursor-pointer text-[0.78rem] font-medium text-muted-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
                Activity · {activityCount}
              </summary>
              <div className="pt-2">
                <ActivityRail state={run} />
              </div>
            </details>
          )
        )}
      </div>

      {settled && <ResultTabs state={run} graph={graph} metrics={metrics} beat={beat} />}
    </article>
  )
}

/**
 * The honest empty state.
 *
 * No placeholder cards, no sample results, no invented domain copy — an empty console
 * has nothing measured to show, and drawing something would be the only untrue thing on
 * the screen. What it does carry is the one action that fills it, and the adapter's own
 * seed questions, which are real configuration rather than a fabricated example.
 */
function EmptyState({
  samples,
  onPick,
}: {
  samples: string[]
  onPick: (question: string) => void
}): ReactElement {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 px-4 py-12 text-center">
      <Sparkles aria-hidden className="size-7 text-muted-foreground/40" />
      <div className="max-w-lg">
        <h2 className="font-display text-lg font-semibold text-foreground">Nothing has run yet</h2>
        <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
          Send a question and watch it run: one card per agent, retrieval and guardrails
          reporting beside them, then the answer with the sources it stands on.
        </p>
      </div>
      {samples.length > 0 && (
        <div className="flex max-w-2xl flex-wrap items-center justify-center gap-1.5">
          <span className="eyebrow mr-1">Try</span>
          {samples.map((sample) => (
            <button
              key={sample}
              type="button"
              onClick={() => onPick(sample)}
              className="max-w-full truncate rounded-md border border-border bg-surface/60 px-2.5 py-1 text-left font-mono text-[0.7rem] text-muted-foreground outline-none transition-colors hover:border-agent/50 hover:text-agent-ink focus-visible:ring-[3px] focus-visible:ring-ring/50"
              title={sample}
            >
              {sample}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * The chat console — session rail, thread, composer.
 *
 * This replaces `MoneyShotConsole`, which was a three-column bento over exactly one run:
 * it had nowhere to put a second question and nowhere to keep the first. Every panel it
 * owned survives, re-homed rather than rewritten — the decision strip and answer in the
 * Answer tab, the rerank scoreboard in Sources, the guardrail glass box, node timeline,
 * trace log, graph and efficiency panel in Trace, and the trust bar, reasoning lane,
 * stream banners and approval spotlight inside the live turn.
 */
export function ChatConsole({ role }: { role: Role }): ReactElement {
  const { session: auth, hydrated } = useAuth()
  const token = auth?.token ?? null

  const chat = useChatThread(token)
  const metrics = useMetrics(token)

  const [graph, setGraph] = useState<GraphResponse>(EMPTY_GRAPH)
  const [personaId, setPersonaId] = useState(personasForRole(role)[0]?.id ?? '')
  const [decided, setDecided] = useState(false)
  const threadEndRef = useRef<HTMLDivElement>(null)

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

  // Follow the newest turn as it arrives.
  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ block: 'end' })
  }, [turnCount])

  const send = (question: string): void => {
    setDecided(false)
    chat.ask(question, personaId)
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
  const memorySubject = memorySubjectFromToken(token)

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

      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex items-center gap-2 lg:hidden">
          <label htmlFor="chat-picker" className="sr-only">
            Chat
          </label>
          <select
            id="chat-picker"
            value={chat.thread.activeSessionId}
            onChange={(event) => chat.selectChat(event.target.value)}
            className="h-9 min-w-0 flex-1 rounded-md border border-input bg-surface/60 px-2.5 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/40"
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

        <div className="flex min-h-0 flex-1 flex-col gap-6">
          {isEmptyChat(current) ? (
            <EmptyState samples={samples} onPick={send} />
          ) : (
            <>
              {current?.restored.map((turn) => (
                <RestoredTurnView key={`restored-${turn.turnIndex}`} turn={turn} />
              ))}
              {current?.turns.map((turn) => (
                <TurnView key={turn.id} turn={turn} graph={graph} metrics={metrics} />
              ))}
            </>
          )}
          <div ref={threadEndRef} />
        </div>

        <div className="sticky bottom-0 bg-background/95 pt-2 pb-1 backdrop-blur">
          <div className="px-1 pb-1">
            <AssistantBot running={chat.running} />
          </div>
          <Composer
            role={role}
            personaId={personaId}
            onPersonaChange={setPersonaId}
            onSend={send}
            running={chat.running}
          />
        </div>
      </div>

      {/* What the agent has learned, beside the conversation that taught it. */}
      <div className="min-w-0 lg:col-span-2 xl:col-span-1">
        <MemoryRail token={token} subject={memorySubject} />
      </div>

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
