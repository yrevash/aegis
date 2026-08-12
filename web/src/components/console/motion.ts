/**
 * Active-beat + reduced-motion helpers (pure, framework-free).
 *
 * The "active-beat" is the console's heartbeat: every stream event carries a
 * subsystem hue (`state.lastSignal`), and on each new event the same hue pulses
 * in lockstep across the newest trace row, the live flow/graph node, and the
 * matching trust chip. These helpers turn the raw `lastSignal` into the values
 * the views need, and expose a testable reduced-motion probe.
 */

import { SIGNALS, type Signal } from '@/config/signals'
import type { LastSignal } from '@/state/runReducer'

/** One synchronized beat: the hue to pulse and the seq that keys the replay. */
export interface Beat {
  signal: Signal
  /** Sequence number of the driving event — change re-fires the animation. */
  seq: number
  /** Ink hex for canvas/SVG marks that cannot use Tailwind classes. */
  hex: string
}

/**
 * Project `state.lastSignal` into a {@link Beat}, or null before the first
 * event. Keying UI on `beat.seq` re-fires the pulse even when two consecutive
 * events share a hue.
 */
export function beatFromSignal(last: LastSignal | null): Beat | null {
  if (last === null) return null
  return { signal: last.signal, seq: last.seq, hex: SIGNALS[last.signal].hex }
}

/** Whether a given signal owns the current beat (drives per-chip pulsing). */
export function isBeatSignal(beat: Beat | null, signal: Signal): boolean {
  return beat !== null && beat.signal === signal
}

/**
 * Whether the viewer asked for reduced motion. SSR/test-safe: returns `false`
 * when `matchMedia` is unavailable (jsdom-less Node), so callers can gate
 * decorative animation without guarding every call site.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
