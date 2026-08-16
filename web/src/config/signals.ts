/**
 * The signal taxonomy — the design's central idea made into data.
 *
 * Each trust subsystem the jury must read owns one hue, reused everywhere it
 * appears (trace panel, graph, KPIs, trust bar). Keeping the mapping here means
 * a single source of truth for colour semantics across the whole console.
 */

import type { RiskLevel, StreamEventType } from '@/lib/stream'

/** A named trust subsystem. */
export type Signal = 'agent' | 'graph' | 'risk' | 'block' | 'ok' | 'ml' | 'neutral'

interface SignalToken {
  /**
   * Tailwind text-colour utility. Uses the readable *ink* tone so labels stay
   * legible on white; the soft fill hue lives on `border`/`bg` instead.
   */
  text: string
  /** Tailwind border-colour utility (soft fill hue). */
  border: string
  /** Tailwind background-tint utility (soft fill hue). */
  bg: string
  /**
   * Raw hex for canvas/SVG contexts that cannot use Tailwind classes. This is
   * the *ink* tone (mirrors `--<signal>-ink`) so marks read on a white surface.
   */
  hex: string
  /** Human label. */
  label: string
}

/**
 * The full signal → token map. Colours mirror the CSS variables in
 * `index.css`: `border`/`bg` use the soft fill hue (`--<signal>`), while
 * `text`/`hex` use the readable ink tone (`--<signal>-ink`).
 */
export const SIGNALS: Record<Signal, SignalToken> = {
  agent: { text: 'text-agent-ink', border: 'border-agent', bg: 'bg-agent/12', hex: '#0e9488', label: 'Reasoning' },
  graph: { text: 'text-graph-ink', border: 'border-graph', bg: 'bg-graph/12', hex: '#1570ef', label: 'Retrieval' },
  risk: { text: 'text-risk-ink', border: 'border-risk', bg: 'bg-risk/15', hex: '#dc6803', label: 'Human gate' },
  block: { text: 'text-block-ink', border: 'border-block', bg: 'bg-block/15', hex: '#d92d20', label: 'Guardrail' },
  ok: { text: 'text-ok-ink', border: 'border-ok', bg: 'bg-ok/15', hex: '#12b76a', label: 'Healthy' },
  ml: { text: 'text-ml-ink', border: 'border-ml', bg: 'bg-ml/12', hex: '#7a5af8', label: 'ML' },
  neutral: { text: 'text-muted-foreground', border: 'border-border', bg: 'bg-surface-2', hex: '#667085', label: 'System' },
}

/** Map a stream event type to the subsystem hue that owns it. */
export function signalForEvent(type: StreamEventType): Signal {
  switch (type) {
    case 'node_started':
    case 'token':
      return 'agent'
    case 'retrieval':
      return 'graph'
    case 'tool_call':
    case 'tool_result':
      return 'agent'
    case 'approval_required':
    case 'approval_queued':
      return 'risk'
    case 'provenance':
      return 'graph'
    case 'guardrail':
    case 'budget_exceeded':
      return 'block'
    case 'run_started':
    case 'run_finished':
      return 'neutral'
    case 'error':
      return 'block'
    default:
      return 'neutral'
  }
}

/** Map a risk level to its signal hue (low is healthy, high is the gate). */
export function signalForRisk(risk: RiskLevel): Signal {
  return risk === 'high' ? 'block' : risk === 'medium' ? 'risk' : 'ok'
}

/**
 * Explicit hues for the known knowledge-graph entity types (the extractor's
 * `_ENTITY_TYPES` vocabulary), so real entity kinds read as distinct colours
 * instead of a hashed near-collision. Unknown kinds fall back to a deterministic
 * hash, keeping the viz legible for any adapter's custom types.
 */
export const KIND_COLORS: Record<string, string> = {
  organization: '#1570ef', // blue
  person: '#7a5af8', // violet
  product: '#0e9488', // teal
  policy: '#dc6803', // amber
  procedure: '#12b76a', // green
  issue: '#d92d20', // rose
  system: '#4b56c9', // indigo
  category: '#0891b2', // cyan
  location: '#db2777', // magenta
  event: '#ea580c', // orange
}

const KIND_PALETTE = ['#1570ef', '#0e9488', '#7a5af8', '#dc6803', '#12b76a', '#d92d20']

export function colorForKind(kind: string): string {
  const known = KIND_COLORS[kind.toLowerCase()]
  if (known) return known
  let hash = 0
  for (let i = 0; i < kind.length; i += 1) {
    hash = (hash * 31 + kind.charCodeAt(i)) >>> 0
  }
  return KIND_PALETTE[hash % KIND_PALETTE.length]
}
