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
  graph: { text: 'text-blue-700', border: 'border-blue-400', bg: 'bg-blue-400/12', hex: '#1570ef', label: 'Retrieval' },
  risk: { text: 'text-risk-ink', border: 'border-risk', bg: 'bg-risk/15', hex: '#b54708', label: 'Human gate' },
  block: { text: 'text-block-ink', border: 'border-block', bg: 'bg-block/15', hex: '#b42318', label: 'Guardrail' },
  ok: { text: 'text-ok-ink', border: 'border-ok', bg: 'bg-ok/15', hex: '#067647', label: 'Healthy' },
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
/**
 * Entity kinds are told apart by their **label**, not by a hue.
 *
 * This was a ten-hue rainbow — violet, teal, amber, green, rose, indigo, cyan,
 * magenta, orange — and it spent all three of the reserved status colours
 * (`#dc6803`, `#12b76a`, `#d92d20`) on entity kinds, which DESIGN.md §2 forbids:
 * a status hue reused as a series colour means a reader can no longer tell which
 * colours carry state.
 *
 * It also could not have worked. `scripts/validate_palette.js` fails every
 * ten-hue set on this surface, and DESIGN.md is explicit that a ninth series is
 * never a generated hue. The canvas already draws `n.label` under every node, so
 * identity was being carried twice — once legibly, once by a colour nobody can
 * hold ten of in their head.
 *
 * What is left encodes something a reader can actually use: two steps on the one
 * blue ramp, so kinds group, with the label carrying which kind. The pair is
 * validated — `node scripts/validate_palette.js "#1570ef,#60a5fa"` → ALL CHECKS
 * PASS, ΔE 15.2 deutan — and the contrast relief the validator asks for is the
 * node label itself.
 */
const KIND_RAMP = ['#1570ef', '#60a5fa'] as const

/** Kinds the shipped adapters emit, grouped rather than individually hued. */
export const KIND_COLORS: Record<string, string> = {
  organization: KIND_RAMP[0],
  person: KIND_RAMP[1],
  product: KIND_RAMP[0],
  policy: KIND_RAMP[1],
  procedure: KIND_RAMP[0],
  issue: KIND_RAMP[1],
  system: KIND_RAMP[0],
  category: KIND_RAMP[1],
  location: KIND_RAMP[0],
  event: KIND_RAMP[1],
}

export function colorForKind(kind: string): string {
  const known = KIND_COLORS[kind.toLowerCase()]
  if (known) return known
  // An adapter's own kind still groups deterministically, on the same two steps.
  let hash = 0
  for (let i = 0; i < kind.length; i += 1) {
    hash = (hash * 31 + kind.charCodeAt(i)) >>> 0
  }
  return KIND_RAMP[hash % KIND_RAMP.length]
}
