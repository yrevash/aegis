/**
 * What the security-posture response actually said — including when it said nothing.
 *
 * Pure and JSX-free so `tests/guardrails/nemoState.test.mjs` can run it directly; the
 * rule it carries is small and it was wrong, which is the combination that earns a
 * module of its own.
 */

import type { SecurityPostureResponse } from '@/lib/api/platform'

/** What this session knows about the Colang engine. Unknown is its own answer. */
export type NemoState = 'available' | 'not installed' | 'unknown'

/**
 * Read the Colang engine's state off the posture signals — three answers, not two.
 *
 * The whole defect is one coalesce. `GET /security/posture` is platform-only —
 * `require_infra_reader` refuses a tenant-pinned principal outright — so `signals` is
 * null for every tenant's own analyst, and `signals?.nemo_available ?? false` turned
 * *"you are not allowed to read this"* into a flat **NOT INSTALLED**, about a package
 * that is installed and that the same endpoint reports as available to platform staff
 * at the same instant. A refusal has to survive as a refusal all the way to the badge,
 * which is the one thing `??` on a nullable boolean cannot do.
 */
export function nemoState(signals: SecurityPostureResponse['signals'] | null): NemoState {
  if (signals == null) return 'unknown'
  return signals.nemo_available ? 'available' : 'not installed'
}

/**
 * Why posture is not on screen — one sentence, written once.
 *
 * Both readers of this — the engine indicator and the OWASP coverage panel — are
 * describing the same missing response, and a second spelling of the reason is a second
 * thing that can drift into disagreeing with the first.
 *
 * @param platformWide Whether this principal may read process-wide facts at all.
 */
export function postureAbsence(platformWide: boolean): { why: string; needed: string } {
  return platformWide
    ? {
        why: 'The posture endpoint did not answer this session.',
        needed: 'A reachable /security/posture.',
      }
    : {
        why: 'Posture describes the deployment, not one tenant.',
        needed: 'A devops or platform-admin account.',
      }
}
