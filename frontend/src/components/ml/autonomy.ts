/**
 * Pure derivation of the conformal **autonomy band** — the graded (not binary)
 * autonomy the platform's ML-in-the-loop story turns on:
 *
 * - **Autonomous** — tight interval / high confidence → act (subject to risk).
 * - **Defer** — wide interval → route to the human approval inbox.
 * - **Abstain** — degenerate / no-coverage → do not act, return insufficient
 *   confidence.
 *
 * Side-effect free so it is unit-testable (see `autonomy.test.ts`).
 */

import type { MLGate } from '@/state/runReducer'
import type { Abstained, AutonomyBandKind } from '@/types/stream'
import type { Signal } from '@/config/signals'

/** The three autonomy bands (plus `null` before a verdict exists). */
export type { AutonomyBandKind }

/** Static per-band presentation (label + subsystem hue). */
const BAND_PRESENTATION: Record<AutonomyBandKind, { label: string; signal: Signal }> = {
  autonomous: { label: 'Autonomous', signal: 'ok' },
  defer: { label: 'Defer', signal: 'risk' },
  abstain: { label: 'Abstain', signal: 'ml' },
}

/** A resolved autonomy readout for the badge. */
export interface AutonomyReadout {
  band: AutonomyBandKind | null
  /** Display label, e.g. "Autonomous". */
  label: string
  /** Subsystem hue for the badge. */
  signal: Signal
  /** One-line reason, or null. */
  reason: string | null
}

/** Build a resolved readout for a concrete band, carrying its reason. */
function readoutFor(band: AutonomyBandKind, reason: string | null): AutonomyReadout {
  const { label, signal } = BAND_PRESENTATION[band]
  return { band, label, signal, reason }
}

/**
 * Resolve the autonomy band from the ML gate readout, an (optional) abstain
 * outcome, and whether the run was queued to the durable inbox.
 *
 * Precedence:
 *   1. abstain terminal event (authoritative),
 *   2. the backend's explicit graded `band` when present (§2.3),
 *   3. legacy fallback: queued / gated → defer, cleared → autonomous.
 */
export function autonomyBand(
  mlGate: MLGate | null,
  abstained: Abstained | null,
  queued = false,
): AutonomyReadout {
  if (abstained) {
    return readoutFor('abstain', abstained.reason)
  }
  // Prefer the backend's explicit graded band over the legacy binary gate.
  if (mlGate?.band != null) {
    return readoutFor(mlGate.band, mlGate.gateReason)
  }
  if (queued) {
    return readoutFor('defer', 'Routed to the approvals inbox')
  }
  if (mlGate?.gated === true) {
    return readoutFor('defer', mlGate.gateReason)
  }
  if (mlGate?.gated === false) {
    return readoutFor('autonomous', mlGate.gateReason)
  }
  return { band: null, label: 'Pending', signal: 'neutral', reason: null }
}
