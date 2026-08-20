/**
 * The signal taxonomy — the design's central idea made into data.
 *
 * Each trust subsystem the jury must read owns a consistent treatment, reused
 * everywhere it appears (trace panel, graph, KPIs, trust bar). Keeping the
 * mapping here means one source of truth for the whole console.
 *
 * The three **subject** signals (agent, graph, ml) are no longer three hues: they
 * are three steps on the one blue ramp, and DESIGN.md §2 is explicit that a
 * reader tells them apart by position, label and weight first. The three
 * **state** signals (risk, block, ok) keep distinct hues because they are the
 * reserved status set — and each one ships with an icon and a word, so identity
 * is never colour alone.
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
   * the *ink* tone, so a mark reads on a white surface. For a chart series use
   * `chartHex` instead — a sequential ramp needs separation this map does not
   * have, because two subjects are deliberately near-identical here.
   */
  hex: string
  /** Human label. */
  label: string
}

/**
 * The full signal → token map. `border`/`bg` carry the soft step, `text`/`hex`
 * the readable ink, and every value here is a token that exists in
 * `app/globals.css` — the teal `#0e9488` and violet `#7a5af8` that sat in `hex`
 * were the last two survivors of the six-hue theme, painting every canvas and
 * SVG mark off-system while the class names beside them had already gone blue.
 */
export const SIGNALS: Record<Signal, SignalToken> = {
  agent: { text: 'text-blue-700', border: 'border-blue-200', bg: 'bg-blue-200/40', hex: '#175cd3', label: 'Reasoning' },
  graph: { text: 'text-blue-600', border: 'border-blue-400', bg: 'bg-blue-400/12', hex: '#1570ef', label: 'Retrieval' },
  risk: { text: 'text-risk-ink', border: 'border-risk', bg: 'bg-risk/15', hex: '#dc6803', label: 'Human gate' },
  block: { text: 'text-block-ink', border: 'border-block', bg: 'bg-block/15', hex: '#d92d20', label: 'Guardrail' },
  ok: { text: 'text-ok-ink', border: 'border-ok', bg: 'bg-ok/15', hex: '#12b76a', label: 'Healthy' },
  ml: { text: 'text-blue-800', border: 'border-blue-100', bg: 'bg-blue-100/60', hex: '#1e40af', label: 'ML' },
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
