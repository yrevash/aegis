'use client'

/**
 * Maps a {@link StreamEvent} to how the trace panel should render it: a signal
 * hue, an icon, a one-line title, and optional detail. Pure and exhaustive, so
 * adding a variant to the union surfaces here as a type error.
 */

import {
  AlertTriangle,
  Ban,
  Brain,
  CircleCheck,
  CircleDot,
  Eraser,
  Flag,
  Inbox,
  Layers,
  Network,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  Timer,
  Wallet,
  Wrench,
  type LucideIcon,
} from 'lucide-react'

import type { Signal } from '@/config/signals'
import { signalForEvent } from '@/config/signals'
import type { GuardStage, StreamEvent } from '@/lib/stream'

/**
 * How each rail stage is named in the trace.
 *
 * A `Record<GuardStage, string>`, so adding a stage to the union in `@/lib/stream`
 * is a type error here until it is given a label. The previous
 * `stage === 'input' ? 'Input' : 'Output'` silently rendered every non-input stage
 * as "Output" — which would have shown a tool-result rail firing on a web page as
 * an output rail firing on the answer.
 */
const GUARD_STAGE_LABEL: Record<GuardStage, string> = {
  input: 'Input',
  output: 'Output',
  tool_result: 'Tool result',
}

/** Presentation for a single trace row. */
export interface TraceDescriptor {
  signal: Signal
  icon: LucideIcon
  title: string
  detail?: string
}

/** Describe how to render one event. */
export function describeEvent(event: StreamEvent): TraceDescriptor {
  const signal = signalForEvent(event.type)
  switch (event.type) {
    case 'run_started':
      return { signal, icon: CircleDot, title: 'Run started', detail: `trace ${event.trace_id}` }
    case 'node_started':
      return { signal, icon: Brain, title: event.label, detail: `node · ${event.node}` }
    case 'node_finished': {
      // e.g. "plan · gpt-4o · 312 tok · 140 ms · $0.0004"
      const tokens = event.prompt_tokens + event.completion_tokens
      const parts = [
        event.node,
        event.model ?? 'local',
        `${tokens} tok`,
        `${event.duration_ms} ms`,
        `$${event.cost_usd.toFixed(4)}`,
      ]
      return { signal: 'agent', icon: Timer, title: `${event.label} · done`, detail: parts.join(' · ') }
    }
    case 'reasoning':
      return { signal: 'agent', icon: Brain, title: 'Reasoning…', detail: event.text }
    case 'guardrail': {
      const layer = event.layer ? ` (${event.layer})` : ''
      const detail =
        event.redactions.length > 0
          ? `${event.reason} · masked ${event.redactions.map((r) => r.kind).join(', ')}`
          : event.reason
      return {
        signal: event.verdict === 'block' ? 'block' : event.verdict === 'redact' ? 'risk' : 'ok',
        icon:
          event.verdict === 'redact' ? Eraser : event.verdict === 'pass' ? ShieldCheck : Ban,
        title: `${GUARD_STAGE_LABEL[event.stage]} guardrail${layer} · ${event.verdict}`,
        detail,
      }
    }
    case 'retrieval': {
      const top = event.scored_sources[0]
      const detail =
        event.status === 'reranked' && top !== undefined
          ? `top: ${top.label} (${top.score.toFixed(2)}) · ${event.scored_sources.length} ranked`
          : event.num_candidates > 0
            ? `${event.num_candidates} candidates · +${event.touched_nodes.length} nodes`
            : undefined
      return {
        signal,
        icon: event.status === 'reranked' ? ScanSearch : Network,
        title: `Retrieval · ${event.status}`,
        detail,
      }
    }
    case 'tool_call':
      return {
        signal,
        icon: Wrench,
        title: `Tool call · ${event.tool}`,
        detail: `risk ${event.risk} · ${JSON.stringify(event.args)}`,
      }
    case 'tool_result':
      return {
        signal: event.ok ? 'ok' : 'block',
        icon: event.ok ? CircleCheck : AlertTriangle,
        title: `Tool result · ${event.ok ? 'ok' : 'failed'}`,
        detail: event.summary,
      }
    case 'approval_required':
      return {
        signal,
        icon: Flag,
        title: 'Awaiting human approval',
        detail: event.action,
      }
    case 'approval_queued': {
      const tier = event.assignee_tier ? ` → ${event.assignee_tier}` : ''
      return {
        signal,
        icon: Inbox,
        title: `Queued to approvals inbox${tier}`,
        detail: event.action,
      }
    }
    case 'provenance': {
      const detail = event.cache_hit
        ? `served from cache${event.cache_kind ? ` · ${event.cache_kind}` : ''}`
        : `${event.origins.join('+')}${event.fusion !== 'none' ? ` · ${event.fusion.toUpperCase()}` : ''}`
      return { signal, icon: Layers, title: 'Provenance', detail }
    }
    case 'budget_exceeded':
      return { signal: 'block', icon: Wallet, title: 'Budget exceeded', detail: event.message }
    case 'token':
      return { signal, icon: Sparkles, title: 'Answer chunk', detail: event.text }
    case 'run_finished':
      return {
        signal: event.status === 'completed' ? 'ok' : 'block',
        icon: CircleCheck,
        title: `Run ${event.status}`,
        detail: `${event.prompt_tokens + event.completion_tokens} tokens · $${event.cost_usd.toFixed(4)}`,
      }
    case 'error':
      return { signal: 'block', icon: AlertTriangle, title: 'Error', detail: event.message }
    default:
      return { signal: 'neutral', icon: CircleDot, title: 'Event' }
  }
}