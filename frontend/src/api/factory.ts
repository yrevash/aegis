/**
 * Selects the run transport for the current environment.
 *
 * Live is the default; the in-browser mock is a labelled fallback (offline or
 * forced). When mock is active it plays the scenario in-browser; otherwise runs stream
 * from the live backend. Both satisfy the same {@link RunTransport} contract, so
 * nothing downstream changes.
 */

import { createMockTransport } from '@/mock/mockTransport'

import { createLiveTransport } from './liveTransport'
import { isMock } from './mode'
import type { RunTransport } from './transport'

/**
 * Create the active {@link RunTransport}. Reads the boot-resolved backend mode
 * ({@link isMock}) so a run uses the live SSE backend by default, or the mock
 * fallback when the boot probe found the backend unreachable.
 */
export function createTransport(): RunTransport {
  return isMock() ? createMockTransport() : createLiveTransport()
}
